"""The sandbox runner: what it lets through, and what it hands back.

The restrictions get their own file (`test_escapes.py`), because the standard
for those is adversarial and mixing them with the happy path makes it too easy
to add a feature test and believe the suite still means what it did.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from econometrica.sandbox import child as child_module
from econometrica.sandbox.policy import ENVELOPE_PREFIX, PRELOAD_MODULES, SandboxLimits
from econometrica.sandbox.runner import run_sandboxed


def _frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=8, freq="D")
    return pd.DataFrame({"x": [1.0, 2, 3, 4, 5, 6, 7, 8]}, index=index)


async def test_ordinary_code_runs_and_returns_its_result() -> None:
    outcome = await run_sandboxed(
        "result = {'scalars': {'mean': float(frame['x'].mean())}}", _frame()
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["scalars"]["mean"] == 4.5


async def test_the_frame_arrives_with_its_columns_and_calendar() -> None:
    outcome = await run_sandboxed(
        "result = {'scalars': {"
        "'rows': float(len(frame)),"
        "'first_year': float(frame.index[0].year),"
        "'columns': float(len(frame.columns))}}",
        _frame(),
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["scalars"] == {"rows": 8.0, "first_year": 2024.0, "columns": 1.0}


async def test_a_non_datetime_index_is_not_turned_into_nulls() -> None:
    """A frame indexed by anything else must survive intact.

    Coercing an integer index through `to_datetime` yields NaT for every row,
    which breaks every join the generated code attempts and reads as the
    code's fault rather than the runner's.
    """
    frame = pd.DataFrame({"x": [1.0, 2.0, 3.0]}, index=["a", "b", "c"])

    outcome = await run_sandboxed(
        "result = {'tables': {'idx': {'columns': ['i'], 'rows': list(frame.index)}}}", frame
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["tables"]["idx"]["rows"] == ["a", "b", "c"]


async def test_missing_values_survive_the_crossing() -> None:
    frame = pd.DataFrame({"x": [1.0, float("nan"), 3.0]})

    outcome = await run_sandboxed(
        "result = {'scalars': {'missing': float(frame['x'].isna().sum())}}", frame
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["scalars"]["missing"] == 1.0


async def test_numpy_values_are_coerced_rather_than_failing_to_serialise() -> None:
    """The most ordinary result a model writes is not JSON.

    `np.float64` is not a `float` and `np.ndarray` is not a `list`, so a naive
    `json.dumps` fails on almost every first draft. Coercing in the child keeps
    the envelope a plain document.
    """
    outcome = await run_sandboxed(
        "import numpy as np\n"
        "result = {'scalars': {'beta': np.float64(1.5)},"
        " 'series': {'fitted': {'y': np.arange(3, dtype=float)}}}",
        _frame(),
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["scalars"]["beta"] == 1.5
    assert outcome.payload["series"]["fitted"]["y"] == [0.0, 1.0, 2.0]


async def test_what_the_code_prints_is_captured_separately_from_the_result() -> None:
    outcome = await run_sandboxed("print('working')\nresult = {'scalars': {'a': 1.0}}", _frame())

    assert outcome.status == "ok", outcome.error
    assert outcome.stdout.strip() == "working"
    assert "working" not in str(outcome.payload)


async def test_code_that_assigns_no_result_is_a_failure_not_an_empty_success() -> None:
    """An empty answer that reads as success is the worst outcome available.

    The Quant Coder would publish a `ResultSet` with nothing in it, and the
    Validator would be asked to sign off on silence.
    """
    outcome = await run_sandboxed("total = frame['x'].sum()", _frame())

    assert outcome.status == "failed"
    assert "result" in outcome.error


async def test_a_syntax_error_comes_back_readable() -> None:
    outcome = await run_sandboxed("result = {", _frame())

    assert outcome.status == "failed"
    assert "SyntaxError" in outcome.error


async def test_a_runtime_error_names_the_generated_code_not_the_runner() -> None:
    outcome = await run_sandboxed("result = {'scalars': {'a': 1 / 0}}", _frame())

    assert outcome.status == "failed"
    assert "ZeroDivisionError" in outcome.error
    assert "child.py" not in outcome.error


async def test_the_statistical_stack_is_available_without_importing_it() -> None:
    """A model that writes `np.` and `pd.` without an import still works.

    Not leniency: the preload has already paid for those imports, and a retry
    burnt on a missing `import numpy as np` teaches nothing.
    """
    outcome = await run_sandboxed(
        "fit = sm.OLS(frame['x'].values, sm.add_constant(np.arange(8.0))).fit()\n"
        "result = {'estimates': [{'name': 'slope', 'value': float(fit.params[1])}]}",
        _frame(),
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["estimates"][0]["value"] == pytest.approx(1.0)


async def test_output_is_truncated_rather_than_returned_whole() -> None:
    outcome = await run_sandboxed(
        "print('x' * 5_000_000)\nresult = {'scalars': {}}",
        _frame(),
        limits=SandboxLimits(max_output_bytes=50_000),
    )

    assert len(outcome.stdout) <= 50_000


def test_the_child_and_the_policy_agree_on_the_envelope_marker() -> None:
    """The child cannot import `policy`, so the marker is a literal there.

    It has to be: `_emit` runs before the payload is parsed, on the path where
    the payload was unreadable. This is the guard that keeps the two spellings
    from drifting into a runner that silently never finds a result.
    """
    source = Path(child_module.__file__).read_text(encoding="utf-8")

    assert f'"{ENVELOPE_PREFIX}"' in source


def test_every_preloaded_module_actually_imports() -> None:
    """A typo here would cost a sandbox start, reported as "could not start".

    Checked in-process rather than through a run, so the failure names the
    module rather than arriving as a subprocess that would not begin.
    """
    import importlib

    for name in PRELOAD_MODULES:
        assert importlib.import_module(name) is not None


def test_the_preloaded_names_are_all_on_the_allowlist() -> None:
    from econometrica.sandbox.policy import ALLOWED_IMPORTS

    roots = {name.split(".")[0] for name in PRELOAD_MODULES}
    assert roots <= ALLOWED_IMPORTS


def test_numpy_is_available_to_the_test_module() -> None:
    # Guards the import above from being pruned as unused by a future cleanup;
    # the sandbox tests compare against numpy semantics deliberately.
    assert np.float64(1.5) == 1.5
