"""The query writer against a real model and a real search engine.

The unit tests prove the wiring; this proves the query. CLAUDE.md records the
measurement this closes: the verbatim NSEI question surfaced no symbol, while
"Nifty 50 ticker symbol Yahoo Finance" returned ^NSEI. Here a real model writes
the query and a real search runs it, and the assertion is that the symbol comes
back — the thing no unit test can promise.

Skips when Ollama or the network is absent, like every other live test. Read the
report, not just the exit code.
"""

import pytest

from econometrica.agents.query_writer import QueryWriter
from econometrica.llm.providers.ollama import OllamaProvider
from econometrica.tools.web_search import build_search_provider

MODEL = "ministral-3:8b"
NSEI_QUESTION = (
    "How has the National Stock Exchange of India grown over the last 10 years?"
)


def _ollama_is_up() -> bool:
    import httpx

    try:
        httpx.get("http://localhost:11434/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return True


def _ddg_is_up() -> bool:
    import httpx

    try:
        httpx.get("https://lite.duckduckgo.com/", timeout=8.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_a_written_query_surfaces_the_nifty_symbol() -> None:
    if not _ollama_is_up():
        pytest.skip("ollama is not running")
    if not _ddg_is_up():
        pytest.skip("duckduckgo is not reachable")

    writer = QueryWriter(OllamaProvider(), MODEL)
    result = await writer.write(NSEI_QUESTION)
    assert result.output.queries, "the writer produced no query"

    provider = build_search_provider("duckduckgo")
    surfaced = ""
    for query in result.output.queries:
        for hit in await provider.search(query, limit=5):
            surfaced += f"{hit.title} {hit.url} {hit.snippet}\n"

    # The motivating case: the written query, unlike the verbatim question,
    # brings back the symbol the Planner needs.
    assert "NSEI" in surfaced.upper(), (
        "expected a written query to surface ^NSEI; got:\n" + surfaced
    )
