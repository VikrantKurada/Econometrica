"""The Query Writer: a question becomes symbol-shaped lookup queries."""

import json

from econometrica.agents.query_writer import QueryWriter, SearchQuery
from econometrica.llm.fake import FakeProvider


def writer(*responses: str) -> tuple[QueryWriter, FakeProvider]:
    fake = FakeProvider(name="q", responses=list(responses))
    return QueryWriter(fake, "fake-1"), fake


async def test_it_writes_a_symbol_shaped_query_for_a_named_index():
    agent, fake = writer(json.dumps({"queries": ["Nifty 50 ticker symbol Yahoo Finance"]}))

    result = await agent.write(
        "How has the National Stock Exchange of India grown over the last 10 years?"
    )

    assert result.output.queries == ["Nifty 50 ticker symbol Yahoo Finance"]
    # The question has to reach the model — the whole point is extracting the
    # instrument name from it.
    sent = "\n".join(m.content for m in fake.calls[0].messages)
    assert "National Stock Exchange of India" in sent


async def test_an_empty_query_list_is_rejected_and_retried():
    agent, fake = writer(
        json.dumps({"queries": []}),
        json.dumps({"queries": ["AAPL ticker symbol Yahoo Finance"]}),
    )

    result = await agent.write("What is Apple's beta?")

    assert result.output.queries == ["AAPL ticker symbol Yahoo Finance"]
    assert len(fake.calls) == 2  # the empty reply spent a retry


async def test_blank_and_duplicate_queries_are_stripped_and_deduped():
    agent, _ = writer(
        json.dumps({"queries": ["  AAPL ticker  ", "AAPL TICKER", "", "   "]})
    )

    result = await agent.write("Apple")

    assert result.output.queries == ["AAPL ticker"]


async def test_one_query_per_instrument_for_a_multi_instrument_question():
    agent, _ = writer(
        json.dumps(
            {"queries": ["Nifty 50 ticker symbol Yahoo Finance", "AAPL ticker symbol"]}
        )
    )

    result = await agent.write("How does AAPL compare with the Nifty 50?")

    assert result.output.queries == [
        "Nifty 50 ticker symbol Yahoo Finance",
        "AAPL ticker symbol",
    ]


def test_the_schema_rejects_an_all_blank_list_directly():
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        SearchQuery(queries=["", "   "])
