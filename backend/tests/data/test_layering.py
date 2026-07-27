"""The data layer sits below the agent layer, and these tests hold it there.

`PriceSource` and `DataUnavailableError` were defined in
`agents/data_steward.py` while the steward was their only consumer. By the fifth
adapter every module in `data/` was importing up into `agents/` to get them —
and the moment the steward needed to call *down* (resolving a risk-free rate
needs `data.rates.resolve_rate`) that became a genuine import cycle rather than
an aesthetic complaint.

The names now live in `data/base.py` and `agents/data_steward.py` re-exports
them, so nothing outside `data/` changed. Two things have to stay true for that
to keep working, and neither shows up in a normal test run: no module in `data/`
may import from `agents/`, and both import orders must succeed.
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from econometrica.agents import data_steward
from econometrica.data import base

DATA_PACKAGE = Path(base.__file__).parent


def imported_modules(path: Path) -> set[str]:
    """Every module named by an import statement, at any nesting depth.

    Parsed rather than grepped so a name inside a docstring or a comment does
    not count — `data/base.py`'s own docstring discusses `agents/data_steward`.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize(
    "module_path",
    sorted(DATA_PACKAGE.glob("*.py")),
    ids=lambda path: path.name,
)
def test_no_data_module_imports_from_the_agent_layer(module_path):
    """The direction of the dependency is the whole point of `data/base.py`.

    A new adapter that reaches up for `DataUnavailableError` would compile,
    pass its own tests, and reintroduce the cycle for whoever next makes the
    steward call into `data/`.
    """
    upward = {
        name
        for name in imported_modules(module_path)
        if name.startswith("econometrica.agents")
    }

    assert upward == set(), (
        f"{module_path.name} imports {sorted(upward)}; import from"
        " econometrica.data.base instead — see this module's docstring"
    )


def test_the_re_export_is_the_same_object():
    """Sixteen import sites still name `agents.data_steward`. If the re-export
    ever became a copy, an `except DataUnavailableError` would stop catching
    what the adapters raise — and it would catch it in most tests, because most
    of them import from one side only."""
    assert data_steward.DataUnavailableError is base.DataUnavailableError
    assert data_steward.PriceSource is base.PriceSource


@pytest.mark.parametrize(
    "first,second",
    [
        ("econometrica.data.rates", "econometrica.agents.data_steward"),
        ("econometrica.agents.data_steward", "econometrica.data.rates"),
    ],
)
def test_both_import_orders_succeed(first, second):
    """The cycle test, and it has to run in a subprocess.

    By the time any test in this suite executes, both modules are already in
    `sys.modules` — imported by something earlier in collection — so an
    in-process import proves nothing at all. This is the same trap
    `tests/api/test_app_startup.py` documents for the tool registry, which
    stayed empty in a live server while every test passed.
    """
    result = subprocess.run(
        [sys.executable, "-c", f"import {first}; import {second}"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
