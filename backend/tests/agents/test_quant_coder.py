"""The Quant Coder: the one agent whose output is not a registry number.

Two things are being tested here and only one of them is the code generation.
The other is the **marking** — a result with no tested function behind it must
never be indistinguishable from one that has, and that is the load-bearing
property of the whole escape hatch.
"""

import json

import pandas as pd
import pytest

from econometrica.agents.quant_coder import (
    UNVALIDATED_VERSION,
    CodeDraft,
    QuantCoder,
    SandboxNotPermittedError,
    check_permitted,
    is_sandbox_result,
)
from econometrica.llm.fake import FakeProvider


def draft(code: str, method: str = "Rolling dispersion") -> str:
    return json.dumps({"method": method, "code": code, "rationale": "no registry tool fits"})


def frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=40, freq="D")
    return pd.DataFrame(
        {"btc_return": [0.01 * ((i % 7) - 3) for i in range(40)]},
        index=index,
    )


GOOD = (
    "spread = float(frame['btc_return'].max() - frame['btc_return'].min())\n"
    "result = {'scalars': {'spread': spread}}"
)


def prompt(provider: FakeProvider, call: int = 0) -> str:
    return "\n".join(message.content for message in provider.calls[call].messages)


# --- what it produces -------------------------------------------------------


async def test_a_working_draft_becomes_a_result() -> None:
    provider = FakeProvider(responses=[draft(GOOD)])

    run = await QuantCoder(provider, "fake-1").compute("dispersion of daily returns", frame())

    assert run.published is True, run.error
    assert run.result is not None
    assert run.result.scalars["spread"] == pytest.approx(0.06)


async def test_the_result_is_marked_as_an_unvalidated_method() -> None:
    """The property the whole feature turns on.

    A registry result names a tested function and its version. This one has
    neither, so it says so in both places anything reads: the tool name and the
    manifest's version.
    """
    provider = FakeProvider(responses=[draft(GOOD, method="Rolling dispersion")])

    run = await QuantCoder(provider, "fake-1").compute("dispersion", frame())

    assert run.result is not None
    assert run.result.tool == "sandbox:rolling_dispersion"
    assert run.result.version == UNVALIDATED_VERSION
    assert run.result.manifest.tool_version == UNVALIDATED_VERSION
    assert is_sandbox_result(run.result) is True


async def test_a_registry_result_is_not_mistaken_for_a_sandbox_one() -> None:
    """`is_sandbox_result` is what the canvas and the exports key off, so a
    false positive would label an ordinary CAPM as unvalidated."""
    from econometrica.econ import load_tools
    from econometrica.econ.registry import get_registry

    load_tools()
    assert get_registry().get("capm") is not None
    # A tool named `sandbox_something` would still not match: the prefix is
    # `sandbox:` and a colon cannot appear in a registry tool name.
    assert is_sandbox_result.__module__.startswith("econometrica")


async def test_the_manifest_fingerprints_the_code_not_just_the_frame() -> None:
    """Two methods over the same data are two analyses.

    A manifest that could not tell them apart would claim otherwise, and
    re-running one from it would reproduce the other.
    """
    coder = QuantCoder(FakeProvider(responses=[draft(GOOD)]), "fake-1")
    first = await coder.compute("dispersion", frame())

    other = "result = {'scalars': {'mean': float(frame['btc_return'].mean())}}"
    second = await QuantCoder(FakeProvider(responses=[draft(other)]), "fake-1").compute(
        "level", frame()
    )

    assert first.result is not None and second.result is not None
    assert first.result.manifest.data_fingerprint == second.result.manifest.data_fingerprint
    assert first.result.manifest.params_hash != second.result.manifest.params_hash


async def test_estimates_and_diagnostics_survive_the_crossing() -> None:
    code = (
        "result = {"
        "'estimates': [{'name': 'slope', 'value': 0.5, 'p_value': 0.01}],"
        "'diagnostics': [{'name': 'made_up', 'statistic': 1.25, 'passed': None}],"
        "'series': {'fitted': {'name': 'fitted', 'x': ['a'], 'y': [1.0]}}}"
    )
    provider = FakeProvider(responses=[draft(code)])

    run = await QuantCoder(provider, "fake-1").compute("anything", frame())

    assert run.published is True, run.error
    assert run.result is not None
    assert run.result.estimate("slope") is not None
    assert run.result.diagnostics[0].passed is None
    assert run.result.series["fitted"].y == [1.0]


async def test_the_numbers_it_produced_are_citable_by_the_narrator() -> None:
    """The escape hatch is the one non-registry source of numbers, by design.

    §2 chose it deliberately and gated it behind the Validator; a result the
    Narrator could not cite would be a result nobody could report.
    """
    from econometrica.agents.grounding import allowed_values

    provider = FakeProvider(responses=[draft(GOOD)])
    run = await QuantCoder(provider, "fake-1").compute("dispersion", frame())

    assert run.result is not None
    assert any(value == pytest.approx(0.06) for value in allowed_values([run.result]))


# --- what it refuses to produce ---------------------------------------------


async def test_a_payload_with_nothing_in_it_is_not_a_result() -> None:
    provider = FakeProvider(responses=[draft("result = {}"), draft("result = {}")])

    run = await QuantCoder(provider, "fake-1").compute("anything", frame())

    assert run.published is False
    assert run.result is None
    assert "nothing" in run.error.lower() or "empty" in run.error.lower()


async def test_a_payload_with_an_unrecognised_key_is_rejected() -> None:
    """`ResultSet` is a closed vocabulary and the canvas draws from it.

    A key nothing renders would be silently dropped, so the model is told
    rather than left believing it reported something.
    """
    code = "result = {'scalars': {'a': 1.0}, 'conclusion': 'markets are efficient'}"
    provider = FakeProvider(responses=[draft(code), draft(code)])

    run = await QuantCoder(provider, "fake-1").compute("anything", frame())

    assert run.published is False
    assert "conclusion" in run.error


async def test_an_escape_attempt_produces_no_result_at_all() -> None:
    code = "import socket\nresult = {'scalars': {'a': 1.0}}"
    provider = FakeProvider(responses=[draft(code), draft(code)])

    run = await QuantCoder(provider, "fake-1").compute("anything", frame())

    assert run.published is False
    assert run.status == "denied"
    assert run.result is None
    assert any("socket" in denial for denial in run.denials)


async def test_a_draft_that_never_assigns_result_is_rejected_before_it_runs() -> None:
    """A contract check, not a security one.

    Catching it here spends a retry on a message the model can act on instead
    of a subprocess that reports "you assigned NoneType".
    """
    provider = FakeProvider(
        responses=[draft("total = frame['btc_return'].sum()"), draft(GOOD)]
    )

    run = await QuantCoder(provider, "fake-1").compute("anything", frame())

    assert run.published is True, run.error
    assert len(provider.calls) == 2
    assert "result" in prompt(provider, 1)


# --- the retry --------------------------------------------------------------


async def test_a_crash_is_shown_to_the_model_and_the_second_draft_runs() -> None:
    broken = "result = {'scalars': {'a': frame['missing'].mean()}}"
    provider = FakeProvider(responses=[draft(broken), draft(GOOD)])

    run = await QuantCoder(provider, "fake-1").compute("dispersion", frame())

    assert run.published is True, run.error
    assert run.attempts == 2
    # The retry has to carry the actual failure; "that did not work" gets the
    # same code back, which is the lesson `agents/base.py` already records.
    assert "KeyError" in prompt(provider, 1)


async def test_two_failures_end_the_attempt_rather_than_looping() -> None:
    broken = "result = {'scalars': {'a': 1 / 0}}"
    provider = FakeProvider(responses=[draft(broken), draft(broken)])

    run = await QuantCoder(provider, "fake-1").compute("anything", frame())

    assert run.published is False
    assert run.attempts == 2
    assert "ZeroDivisionError" in run.error
    assert len(provider.calls) == 2


# --- the prompt -------------------------------------------------------------


async def test_the_prompt_describes_the_frame_and_the_contract() -> None:
    provider = FakeProvider(responses=[draft(GOOD)])

    await QuantCoder(provider, "fake-1").compute("dispersion of daily returns", frame())

    text = prompt(provider)
    assert "btc_return" in text
    assert "dispersion of daily returns" in text
    assert "result" in text
    # The import allowlist has to be in the prompt or the model spends its
    # attempts discovering it one refusal at a time.
    assert "numpy" in text and "statsmodels" in text
    assert "os" not in text.split("allowed", 1)[-1].split("\n")[0]


# --- the gate ---------------------------------------------------------------


def test_the_sandbox_is_refused_when_the_capability_is_off() -> None:
    with pytest.raises(SandboxNotPermittedError, match="not enabled"):
        check_permitted(enabled=False, tier="critic")


def test_the_sandbox_is_refused_in_the_tier_that_skips_the_validator() -> None:
    """§2 makes Validator sign-off mandatory, and `single` has no Validator.

    Running anyway would put an unreviewed, model-authored number in front of a
    reader, which is the exact thing the design chose option C to avoid.
    """
    with pytest.raises(SandboxNotPermittedError, match="single"):
        check_permitted(enabled=True, tier="single")


def test_the_sandbox_is_permitted_when_both_conditions_hold() -> None:
    check_permitted(enabled=True, tier="critic")
    check_permitted(enabled=True, tier="consensus")


def test_a_draft_rejects_empty_code_at_the_schema() -> None:
    with pytest.raises(ValueError, match="code"):
        CodeDraft(method="x", code="")
