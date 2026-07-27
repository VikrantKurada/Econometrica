"""The one genuinely model-shaped part of an upload.

The profiler decides what each column *could* be; this agent picks among those
candidates where the choice is real. Everything it is forbidden to do has a
test, because the failure mode is not a crash — it is a plausible mapping that
ingests the wrong column as prices.

It is also allowed to be skipped entirely. A file whose every column has one
obvious role is not a question, and asking anyway would put a billed turn
behind every upload.
"""

import json

import pandas as pd
import pytest

from econometrica.agents.column_mapper import ColumnMapper
from econometrica.llm.fake import FakeProvider
from econometrica.services.ingest import profile_upload

#: `v` is a large positive integer column with no telling name, so the profiler
#: offers both `volume` and `price` and neither wins outright.
AMBIGUOUS = pd.DataFrame(
    {
        "date": pd.date_range("2024-01-01", periods=6, freq="D").astype(str),
        "v": [1200, 1300, 1250, 1400, 1350, 1500],
    }
)

CLEAR = pd.DataFrame(
    {
        "date": pd.date_range("2024-01-01", periods=6, freq="D").astype(str),
        "AAPL": [100.0, 101.0, 102.5, 101.5, 103.0, 104.0],
    }
)


def profile_of(tmp_path, frame: pd.DataFrame):
    path = tmp_path / "u.csv"
    frame.to_csv(path, index=False)
    return profile_upload(path)


def reply(*choices: tuple[str, str]) -> str:
    return json.dumps(
        {"columns": [{"column": c, "role": r, "reason": "because"} for c, r in choices]}
    )


# --- when the model is not needed --------------------------------------------


async def test_an_unambiguous_file_costs_no_model_call(tmp_path):
    provider = FakeProvider(responses=[reply(("AAPL", "return"))])

    proposal, result = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, CLEAR)
    )

    assert provider.calls == []
    assert result is None
    assert proposal.roles["AAPL"] == "price"


# --- when it is ---------------------------------------------------------------


async def test_an_ambiguous_column_is_put_to_the_model(tmp_path):
    provider = FakeProvider(responses=[reply(("v", "price"))])

    proposal, result = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    assert len(provider.calls) == 1
    assert result is not None
    assert proposal.roles["v"] == "price"


async def test_the_models_reason_replaces_the_profilers(tmp_path):
    provider = FakeProvider(
        responses=[
            json.dumps(
                {
                    "columns": [
                        {"column": "v", "role": "price", "reason": "the file is a NAV series"}
                    ]
                }
            )
        ]
    )

    proposal, _ = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    assert proposal.rationale["v"] == "the file is a NAV series"


async def test_columns_the_model_says_nothing_about_keep_their_proposal(tmp_path):
    provider = FakeProvider(responses=[reply(("v", "price"))])

    proposal, _ = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    assert proposal.roles["date"] == "date"


async def test_only_the_ambiguous_columns_are_described_to_the_model(tmp_path):
    """A model asked to re-decide everything will re-decide something that was
    never in question."""
    provider = FakeProvider(responses=[reply(("v", "price"))])

    await ColumnMapper(provider, "fake-1").propose(profile_of(tmp_path, AMBIGUOUS))

    prompt = "\n".join(m.content for m in provider.calls[0].messages)
    assert "v" in prompt
    assert "volume" in prompt and "price" in prompt


# --- what it may not do -------------------------------------------------------


async def test_a_column_the_file_does_not_have_is_rejected_and_retried(tmp_path):
    """The retry has to name the problem, or the next draft repeats it."""
    provider = FakeProvider(responses=[reply(("GOOG", "price")), reply(("v", "volume"))])

    proposal, result = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    assert len(provider.calls) == 2
    retry = "\n".join(m.content for m in provider.calls[1].messages)
    assert "GOOG" in retry
    assert proposal.roles["v"] == "volume"
    assert result is not None


async def test_a_role_the_profiler_ruled_out_is_rejected_and_retried(tmp_path):
    """The model's whole remit is choosing among admissible candidates. `date`
    was never on offer for a column of integers, and letting it through would
    put the calendar on the wrong column."""
    provider = FakeProvider(responses=[reply(("v", "date")), reply(("v", "volume"))])

    proposal, _ = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    retry = "\n".join(m.content for m in provider.calls[1].messages)
    assert "date" in retry
    assert proposal.roles["v"] == "volume"


async def test_an_invented_role_is_rejected(tmp_path):
    provider = FakeProvider(
        responses=[
            json.dumps({"columns": [{"column": "v", "role": "sentiment"}]}),
            reply(("v", "volume")),
        ]
    )

    proposal, _ = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    assert proposal.roles["v"] == "volume"


async def test_a_model_that_never_complies_falls_back_to_the_profiler(tmp_path):
    """An upload must not fail because a model would not answer. The
    deterministic proposal is still a good suggestion, and the user confirms it
    either way — refusing the whole upload would be a worse outcome than
    showing the profiler's own guess.
    """
    provider = FakeProvider(responses=[reply(("GOOG", "price"))] * 3)

    proposal, result = await ColumnMapper(provider, "fake-1").propose(
        profile_of(tmp_path, AMBIGUOUS)
    )

    assert proposal.roles["v"] == "volume"
    assert result is None


# --- the result is still only a proposal -------------------------------------


async def test_what_comes_back_is_not_confirmed(tmp_path):
    """Whatever the model said, a person still has to agree to it."""
    from econometrica.services.mapping import ColumnMapping, MappingError, apply_mapping

    provider = FakeProvider(responses=[reply(("v", "price"))])
    profile = profile_of(tmp_path, AMBIGUOUS)

    proposal, _ = await ColumnMapper(provider, "fake-1").propose(profile)

    with pytest.raises(MappingError, match="confirm"):
        apply_mapping(AMBIGUOUS, ColumnMapping(roles=proposal.roles), profile)
