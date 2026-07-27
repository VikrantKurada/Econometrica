"""Web search: off by default, attributed, and never a source of numbers.

Three properties, none of them about search quality.

**Off unless asked for.** §9 makes web search a per-project setting a chat
inherits and can override, both off by default. The gate reads the *resolved*
capability rather than the project's flag, so a chat that turned it off is
honoured — and a disabled search never reaches the provider at all, rather than
fetching and discarding.

**Attributed.** Every search becomes a trace step naming the provider and the
query, and every result carries its URL. A result reaching a prompt without its
source is a claim from nowhere.

**Never a source of numbers.** The grounding gate admits only values a tool in
`econ/` computed. A figure a model read on a web page is exactly as ungrounded
as one it invented, and a test asserts the gate still blocks one quoted verbatim
from a result.

A failed search **degrades the run rather than failing it**. Search is context,
not a result: a run that could not reach a search engine has less to say, not
nothing, and raising would lose the analysis the user actually asked for.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from econometrica.agents.trace import StepRecord
from econometrica.services.capabilities import ResolvedCapabilities

DEFAULT_LIMIT = 5
DEFAULT_TIMEOUT = 20.0

#: Kept on the trace step. The query and provider are the audit; the results
#: themselves are in the prompt where they were used.
_DETAIL_LIMIT = 300


class SearchDisabledError(PermissionError):
    """Search was attempted while the capability is off."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
        ...


@dataclass(frozen=True)
class SearchOutcome:
    """One search, in the shape the trace and the prompt both want."""

    provider: str
    query: str
    results: list[SearchResult] = field(default_factory=list)
    failed: bool = False
    detail: str = ""

    def as_context(self) -> str:
        """The results as prompt text, each attributed.

        The header marks the text as *read* rather than *computed*, the same
        wording retrieval uses. It is for the model; the grounding gate is what
        actually enforces it.
        """
        if not self.results:
            return ""
        blocks = [
            f"[{result.title} — {result.url}]\n{result.snippet}"
            for result in self.results
        ]
        return (
            "# Web search results — read from the web, not computed.\n"
            "Nothing here is a result. Do not cite a number from it.\n\n"
            + "\n\n".join(blocks)
        )

    def to_step_record(self) -> StepRecord:
        return StepRecord(
            agent="planner",
            kind="tool",
            status="failed" if self.failed else "ok",
            tool=f"web_search:{self.provider}",
            detail=(
                f"{self.query} — {self.detail}"
                if self.failed
                else f"{self.query} — {len(self.results)} result(s)"
            )[:_DETAIL_LIMIT],
        )


async def search(
    query: str,
    *,
    provider: SearchProvider,
    capabilities: ResolvedCapabilities,
    limit: int = DEFAULT_LIMIT,
) -> SearchOutcome:
    """Search the web, if this chat is allowed to."""
    if not capabilities.web_search:
        # Raised rather than returned empty: asking with the capability off is a
        # programming error, and an empty result would hide it.
        raise SearchDisabledError(
            "web search is off for this chat. It is a per-project setting that a"
            " chat inherits and may override, and it is off by default."
        )

    try:
        results = await provider.search(query, limit=limit)
    except Exception as exc:
        return SearchOutcome(
            provider=provider.name, query=query, failed=True, detail=str(exc)[:_DETAIL_LIMIT]
        )

    return SearchOutcome(provider=provider.name, query=query, results=list(results[:limit]))


# --- providers ------------------------------------------------------------------


@dataclass(frozen=True)
class SearchProviderSpec:
    name: str
    label: str
    requires_key: bool
    key_url: str = ""


SEARCH_PROVIDERS: tuple[SearchProviderSpec, ...] = (
    SearchProviderSpec(
        name="duckduckgo",
        label="DuckDuckGo",
        # No key, which is what keeps the zero-configuration path intact.
        requires_key=False,
    ),
    SearchProviderSpec(
        name="brave",
        label="Brave Search",
        requires_key=True,
        key_url="https://brave.com/search/api/",
    ),
)


#: `class='result-link'` — **single** quotes in the served HTML. A pattern
#: written with double quotes matches nothing, which is how this parser was
#: wrong the first time.
_LINK = re.compile(
    r"<a[^>]+class=['\"]result-link['\"][^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", re.S
)
#: The attributes in the other order, since nothing guarantees which comes first.
_LINK_ALT = re.compile(
    r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]*class=['\"]result-link['\"][^>]*>(.*?)</a>", re.S
)
_SNIPPET = re.compile(r"<td[^>]*class=['\"]result-snippet['\"][^>]*>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")


class DuckDuckGoSearch:
    """The keyless default, over the lite endpoint.

    **This reads an HTML page with no API contract behind it**, so it is the
    fragile part of this module and it has a live test for exactly that reason.
    It is a plain POST that the endpoint answers — unlike Stooq, nothing here
    defeats a bot check, and if that ever changes this provider should be
    dropped rather than made to work.
    """

    name = "duckduckgo"
    url = "https://lite.duckduckgo.com/lite/"

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            response = await client.post(
                self.url,
                data={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            )
            response.raise_for_status()

        html = response.text
        links = _LINK.findall(html) or _LINK_ALT.findall(html)
        snippets = _SNIPPET.findall(html)

        results: list[SearchResult] = []
        for index, (href, title) in enumerate(links[:limit]):
            results.append(
                SearchResult(
                    title=_text(title),
                    url=href,
                    snippet=_text(snippets[index]) if index < len(snippets) else "",
                )
            )
        return results


class BraveSearch:
    """The keyed option, over a documented JSON API."""

    name = "brave"
    url = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not api_key:
            raise ValueError("brave search needs an API key")
        self._api_key = api_key
        self._timeout = timeout

    async def search(self, query: str, *, limit: int = DEFAULT_LIMIT) -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                self.url,
                params={"q": query, "count": limit},
                headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()

        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                snippet=_text(str(item.get("description", ""))),
            )
            for item in (payload.get("web", {}).get("results") or [])[:limit]
        ]


def build_search_provider(name: str, *, api_key: str = "") -> SearchProvider:
    """Construct a provider by name — the one place that knows them all."""
    spec = next((entry for entry in SEARCH_PROVIDERS if entry.name == name), None)
    if spec is None:
        raise KeyError(
            f"unknown search provider {name!r}; known:"
            f" {', '.join(entry.name for entry in SEARCH_PROVIDERS)}"
        )
    if spec.requires_key and not api_key:
        # Said here rather than after an unauthenticated request, whose error
        # page would be the vendor's words rather than a usable explanation.
        raise ValueError(f"{name} needs an API key; add one in the provider settings")

    if name == "brave":
        return BraveSearch(api_key=api_key)
    return DuckDuckGoSearch()


def _text(html: str) -> str:
    """Markup as the text a person would read."""
    import html as html_module

    return html_module.unescape(_TAGS.sub("", html)).strip()
