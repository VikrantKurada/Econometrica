"""Web search: off by default, attributed, and never a source of numbers.

Three properties, and none of them is about search quality.

**Off unless asked for.** §9 makes web search a per-project setting that a chat
inherits and can override, both off by default. The gate here reads the
*resolved* capability rather than the project's own flag, so a chat that turned
it off is honoured.

**Attributed.** A result reaching a prompt without its URL is a claim from
nowhere. Every search becomes a trace step naming the provider and the query.

**Never a source of numbers.** The same invariant as retrieval: the grounding
gate admits only what a tool computed. A figure a model read on a web page is
exactly as ungrounded as one it invented.
"""

import pytest

from econometrica.db.models import Chat, Project
from econometrica.services.capabilities import resolve_capabilities
from econometrica.tools.web_search import (
    SearchDisabledError,
    SearchResult,
    search,
)


class FakeSearch:
    name = "fake"

    def __init__(self, results=None, error: Exception | None = None) -> None:
        self.results = results if results is not None else [
            SearchResult(
                title="Fama-French three-factor model",
                url="https://en.wikipedia.org/wiki/Fama-French_three-factor_model",
                snippet="A model expanding on CAPM. The published beta is 1.8100.",
            )
        ]
        self.error = error
        self.queries: list[str] = []

    async def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.results[:limit]


def capabilities(*, project_on: bool = True, chat_override: bool | None = None):
    project = Project(name="P", web_search_enabled=project_on)
    chat = Chat(name="C", web_search_enabled=chat_override)
    return resolve_capabilities(project, chat)


# --- off by default -------------------------------------------------------------


async def test_search_is_refused_when_the_project_has_it_off():
    """The default. §9 has web search off, and off has to mean the provider is
    never reached — not that its results are discarded afterwards."""
    provider = FakeSearch()

    with pytest.raises(SearchDisabledError):
        await search("capm", provider=provider, enabled=capabilities(project_on=False).web_search)

    assert provider.queries == []


async def test_a_new_project_has_it_off():
    project = Project(name="P")
    chat = Chat(name="C")

    assert resolve_capabilities(project, chat).web_search is False


async def test_a_chat_may_turn_it_off_even_where_the_project_allows_it():
    provider = FakeSearch()

    with pytest.raises(SearchDisabledError):
        await search(
            "capm",
            provider=provider,
            enabled=capabilities(project_on=True, chat_override=False).web_search,
        )

    assert provider.queries == []


async def test_a_chat_may_turn_it_on_where_the_project_allows_it():
    outcome = await search(
        "capm", provider=FakeSearch(), enabled=capabilities(project_on=True).web_search
    )

    assert outcome.results


# --- attribution ------------------------------------------------------------------


async def test_every_result_carries_its_url():
    outcome = await search("capm", provider=FakeSearch(), enabled=capabilities().web_search)

    assert all(result.url.startswith("https://") for result in outcome.results)


async def test_a_search_becomes_a_trace_step_naming_the_provider_and_query():
    """§9 asks for results attributed in the trace. A search nobody can audit is
    a set of claims from nowhere."""
    outcome = await search(
        "fama french", provider=FakeSearch(), enabled=capabilities().web_search
    )

    step = outcome.to_step_record()

    assert step.kind == "tool"
    assert step.tool == "web_search:fake"
    assert step.status == "ok"
    assert "fama french" in step.detail


async def test_the_context_names_every_source():
    outcome = await search("capm", provider=FakeSearch(), enabled=capabilities().web_search)

    context = outcome.as_context()

    assert "https://en.wikipedia.org" in context
    assert "Fama-French" in context


async def test_the_context_says_the_text_was_read_not_computed():
    """The same header retrieval uses. It is for the model; the grounding gate
    is what actually enforces it."""
    outcome = await search("capm", provider=FakeSearch(), enabled=capabilities().web_search)

    assert "not computed" in outcome.as_context().lower()


async def test_an_empty_result_set_produces_no_context():
    outcome = await search(
        "capm", provider=FakeSearch(results=[]), enabled=capabilities().web_search
    )

    assert outcome.as_context() == ""
    assert outcome.results == []


# --- degradation ------------------------------------------------------------------


async def test_a_provider_that_is_down_degrades_the_run_rather_than_failing_it():
    """Search is context, not a result. A run that could not reach a search
    engine has less to say, not nothing — and turning that into an exception
    would lose the analysis the user actually asked for."""
    provider = FakeSearch(error=RuntimeError("connection refused"))

    outcome = await search("capm", provider=provider, enabled=capabilities().web_search)

    assert outcome.failed is True
    assert outcome.results == []
    assert "connection refused" in outcome.detail


async def test_a_failed_search_is_still_a_trace_step():
    provider = FakeSearch(error=RuntimeError("connection refused"))

    outcome = await search("capm", provider=provider, enabled=capabilities().web_search)

    assert outcome.to_step_record().status == "failed"


async def test_a_failed_search_contributes_no_context():
    provider = FakeSearch(error=RuntimeError("connection refused"))

    outcome = await search("capm", provider=provider, enabled=capabilities().web_search)

    assert outcome.as_context() == ""


async def test_the_limit_is_passed_through():
    provider = FakeSearch(
        results=[SearchResult(title=f"r{n}", url=f"https://x/{n}", snippet="") for n in range(9)]
    )

    outcome = await search("capm", provider=provider, enabled=capabilities().web_search, limit=3)

    assert len(outcome.results) == 3


# --- the invariant ----------------------------------------------------------------


async def test_a_number_found_on_a_web_page_is_still_ungrounded():
    """The invariant retrieval has too. The grounding gate admits only what a
    tool computed, and a search result is not a tool — so a figure a model read
    on a page is exactly as ungrounded as one it invented.

    Web search must not become a side channel into the one mechanical
    anti-hallucination check the system has.
    """
    from econometrica.agents.grounding import allowed_values, check_grounding
    from econometrica.econ.types import Estimate, Manifest, ResultSet

    outcome = await search("capm", provider=FakeSearch(), enabled=capabilities().web_search)
    assert "1.8100" in outcome.as_context()

    computed = ResultSet(
        tool="capm",
        version="1.0.0",
        params={},
        estimates=[Estimate(name="beta", value=1.273)],
        manifest=Manifest(data_fingerprint="a", tool="capm", tool_version="1.0.0"),
    )

    report = check_grounding("The beta is 1.8100.", allowed_values([computed]))

    assert report.grounded is False


# --- the registry -------------------------------------------------------------------


def test_every_registered_provider_can_be_named():
    from econometrica.tools.web_search import SEARCH_PROVIDERS, build_search_provider

    for spec in SEARCH_PROVIDERS:
        provider = build_search_provider(spec.name, api_key="x" if spec.requires_key else "")
        assert provider.name == spec.name


def test_an_unknown_provider_names_the_known_ones():
    from econometrica.tools.web_search import build_search_provider

    with pytest.raises(KeyError, match="duckduckgo"):
        build_search_provider("altavista")


def test_a_keyed_provider_without_a_key_is_refused():
    """Better to say so at construction than to send an unauthenticated request
    and report whatever the vendor's error page says."""
    from econometrica.tools.web_search import build_search_provider

    with pytest.raises(ValueError, match="key"):
        build_search_provider("brave", api_key="")


def test_the_keyless_provider_needs_no_key():
    from econometrica.tools.web_search import build_search_provider

    assert build_search_provider("duckduckgo").name == "duckduckgo"


# --- live -----------------------------------------------------------------------------


def _reachable() -> bool:
    import httpx

    try:
        httpx.get("https://lite.duckduckgo.com/", timeout=8.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_a_real_search_returns_attributed_results():
    """Against the real endpoint. The parser is the fragile part — it reads an
    HTML page with no API contract behind it — so this is the test that says
    whether it still works."""
    from econometrica.tools.web_search import build_search_provider

    if not _reachable():
        pytest.skip("duckduckgo is not reachable")

    results = await build_search_provider("duckduckgo").search(
        "fama french three factor model", limit=5
    )

    assert results, "expected at least one result"
    assert all(result.url.startswith("http") for result in results)
    assert all(result.title for result in results)
    assert any("wikipedia" in result.url for result in results)
