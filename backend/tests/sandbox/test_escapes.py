"""One escape attempt per restriction. The parent plan asks for exactly this.

Two things make these tests worth more than a list of `pytest.raises`:

* **They try to get out, not merely to be refused.** `SMUGGLE` defeats the
  import allowlist outright — it reaches the *real* `__import__` through an
  allowlisted module's own globals — so every test below that uses it is
  asserting the audit hook holds after the weakest layer has already fallen.
  That is the layering claim from the design note, made mechanical.
* **A refusal is not the assertion where an effect is observable.** An
  exception says a call raised. Only a missing file says nothing was written,
  and only an intact file says nothing was deleted, so those are asserted too.
"""

from pathlib import Path

import pandas as pd
import pytest

from econometrica.sandbox.policy import ALLOWED_IMPORTS, FORBIDDEN_IMPORTS, SandboxLimits
from econometrica.sandbox.runner import run_sandboxed

#: Defeats the import allowlist. A real module's `__globals__` carry the real
#: builtins, gated `__import__` or not — this is the bypass the design note
#: names, written out so the tests underneath it mean what they claim.
SMUGGLE = "import warnings\nsmuggled = warnings.__dict__['__builtins__']['__import__']\n"


def _frame() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=6, freq="D")
    return pd.DataFrame({"x": [1.0, 2, 3, 4, 5, 6]}, index=index)


async def _run(code: str, **kwargs: object) -> object:
    limits = SandboxLimits(**kwargs) if kwargs else SandboxLimits()  # type: ignore[arg-type]
    return await run_sandboxed(code, _frame(), limits=limits)


# --- the smuggling route itself --------------------------------------------


async def test_the_import_allowlist_is_genuinely_bypassable() -> None:
    """The premise every test below depends on.

    If this ever starts failing the import gate has become stronger, which is
    good — but the tests that rely on the bypass would then be asserting
    nothing, so this failing is a signal to rewrite them, not to celebrate.
    """
    outcome = await _run(SMUGGLE + "result = {'scalars': {'reached': 1.0}}")

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["scalars"]["reached"] == 1.0


# --- no network -------------------------------------------------------------


async def test_importing_socket_is_refused() -> None:
    outcome = await _run("import socket\nresult = {}")

    assert outcome.status == "denied"
    assert "socket" in outcome.error


async def test_a_smuggled_socket_still_cannot_be_opened() -> None:
    outcome = await _run(
        SMUGGLE + "s = smuggled('socket')\n"
        "sock = s.socket(s.AF_INET, s.SOCK_STREAM)\nresult = {}"
    )

    assert outcome.status == "denied"
    assert "socket.__new__" in " ".join(outcome.denials)


async def test_dns_resolution_is_refused() -> None:
    outcome = await _run(
        SMUGGLE + "s = smuggled('socket')\nout = s.getaddrinfo('example.com', 80)\nresult = {}"
    )

    assert outcome.status == "denied"


async def test_an_http_request_is_refused() -> None:
    outcome = await _run(
        SMUGGLE + "u = smuggled('urllib.request', fromlist=['request'])\n"
        "u.urlopen('http://example.com', timeout=2)\nresult = {}"
    )

    assert outcome.status == "denied"


# --- no filesystem ----------------------------------------------------------


async def test_writing_a_file_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    target = tmp_path / "escaped.txt"
    outcome = await _run(f"open({str(target)!r}, 'w').write('out')\nresult = {{}}")

    assert outcome.status == "denied"
    # The assertion that matters: an exception says the call raised, only this
    # says nothing reached the disk.
    assert not target.exists()


async def test_deleting_a_file_is_refused_and_the_file_survives(tmp_path: Path) -> None:
    target = tmp_path / "keep.txt"
    target.write_text("still here", encoding="utf-8")

    outcome = await _run(SMUGGLE + f"smuggled('os').remove({str(target)!r})\nresult = {{}}")

    assert outcome.status == "denied"
    assert target.read_text(encoding="utf-8") == "still here"


async def test_reading_a_file_outside_the_runtime_is_refused(tmp_path: Path) -> None:
    secret = tmp_path / "keys.enc"
    secret.write_text("SUPER-SECRET-VALUE", encoding="utf-8")

    outcome = await _run(
        f"data = open({str(secret)!r}).read()\nprint(data)\nresult = {{'scalars': {{}}}}"
    )

    assert outcome.status == "denied"
    assert "SUPER-SECRET-VALUE" not in outcome.stdout
    assert "SUPER-SECRET-VALUE" not in str(outcome.payload)


async def test_listing_a_directory_outside_the_runtime_is_refused(tmp_path: Path) -> None:
    outcome = await _run(
        SMUGGLE + f"out = smuggled('os').listdir({str(tmp_path)!r})\nresult = {{}}"
    )

    assert outcome.status == "denied"


async def test_the_libraries_may_still_read_their_own_files() -> None:
    """The read rule has to be a path rule, not a ban.

    `arch` imports `pyarrow.pandas_compat` at *fit* time, so a blanket ban on
    `open` makes a GARCH impossible — which is how the first draft of this
    policy was wrong.
    """
    outcome = await _run(
        "import arch, pandas as pd, numpy as np\n"
        "rng = np.random.default_rng(3)\n"
        "series = pd.Series(rng.normal(size=400) * 2)\n"
        "fit = arch.arch_model(series).fit(disp='off')\n"
        "result = {'scalars': {'omega': float(fit.params['omega'])}}",
        wall_seconds=60.0,
    )

    assert outcome.status == "ok", f"{outcome.error} / {outcome.denials}"
    assert outcome.payload["scalars"]["omega"] > 0


# --- no other processes -----------------------------------------------------


async def test_importing_subprocess_is_refused() -> None:
    outcome = await _run("import subprocess\nresult = {}")

    assert outcome.status == "denied"
    assert "subprocess" in outcome.error


async def test_a_smuggled_popen_still_cannot_run(tmp_path: Path) -> None:
    marker = tmp_path / "spawned.txt"
    # `!r` rather than plain interpolation: a Windows path dropped straight
    # into generated source turns `C:\Users` into an invalid `\U` escape, and
    # the run then fails on a SyntaxError while looking like a passing refusal.
    outcome = await _run(
        SMUGGLE + "sp = smuggled('subprocess')\n"
        f"sp.run(['cmd', '/c', 'echo', 'hi', '>', {str(marker)!r}])\nresult = {{}}"
    )

    assert outcome.status == "denied"
    assert not marker.exists()


async def test_popen_reached_through_subclasses_still_cannot_run() -> None:
    """The bypass that defeats *both* the import gate and the smuggling route.

    `().__class__.__base__.__subclasses__()` walks to `subprocess.Popen`
    without importing anything at all. It is reachable — the test asserts that
    it is, so this cannot pass by the class merely being absent — and calling
    it is still refused by the audit hook.
    """
    outcome = await _run(
        "found = [c for c in ().__class__.__base__.__subclasses__()"
        " if c.__name__ == 'Popen']\n"
        "assert found, 'Popen was not reachable; this test proves nothing'\n"
        "found[0](['cmd', '/c', 'echo hi'])\n"
        "result = {}"
    )

    assert outcome.status == "denied"
    assert "subprocess.Popen" in " ".join(outcome.denials)


async def test_os_system_is_refused() -> None:
    outcome = await _run(SMUGGLE + "smuggled('os').system('echo hi')\nresult = {}")

    assert outcome.status == "denied"


# --- no reaching into the interpreter or the application --------------------


async def test_ctypes_is_refused() -> None:
    outcome = await _run(SMUGGLE + "smuggled('ctypes').CDLL('kernel32')\nresult = {}")

    assert outcome.status == "denied"


@pytest.mark.parametrize("module", ["os", "sys", "ctypes", "importlib", "shutil", "econometrica"])
async def test_the_import_allowlist_refuses_each_dangerous_module(module: str) -> None:
    outcome = await _run(f"import {module}\nresult = {{}}")

    assert outcome.status == "denied"
    assert module in outcome.error


async def test_dunder_import_is_gated_too() -> None:
    outcome = await _run("__import__('os')\nresult = {}")

    assert outcome.status == "denied"


async def test_importlib_cannot_be_used_to_route_around_the_gate() -> None:
    outcome = await _run("import importlib\nimportlib.import_module('os')\nresult = {}")

    assert outcome.status == "denied"


def test_the_allowlist_and_the_forbidden_list_are_disjoint() -> None:
    """A guard against the realistic way this gate gets widened.

    Nothing enforces `FORBIDDEN_IMPORTS` at runtime — the allowlist already
    denies everything absent from it. This exists so that adding `os` to
    `ALLOWED_IMPORTS` in a hurry fails a test rather than passing a review.
    """
    assert set() == ALLOWED_IMPORTS & FORBIDDEN_IMPORTS


# --- the caps ---------------------------------------------------------------


async def test_an_infinite_loop_is_stopped_at_the_wall_clock_cap() -> None:
    outcome = await _run("while True:\n    pass\nresult = {}", wall_seconds=3.0)

    assert outcome.status == "timeout"
    assert outcome.duration_ms < 12_000, "the kill did not take effect promptly"


async def test_allocating_past_the_memory_cap_is_stopped() -> None:
    outcome = await _run(
        "blocks = []\n"
        "for _ in range(4000):\n"
        "    blocks.append(bytearray(1024 * 1024))\n"
        "result = {}",
        memory_bytes=384 * 1024 * 1024,
        wall_seconds=60.0,
    )

    assert outcome.status == "out_of_memory", f"{outcome.status}: {outcome.error}"


# --- the envelope itself ----------------------------------------------------


async def test_generated_code_cannot_forge_the_result_envelope() -> None:
    """The child's answer is the *last* marked line, and it always writes one.

    Generated code printing a marked line is the obvious way to claim a result
    it did not compute, so the forged line has to lose to the real one.
    """
    outcome = await _run(
        "print('__ECONOMETRICA_SANDBOX__' + '{\"status\": \"ok\", \"result\":"
        ' {"scalars": {"beta": 99.0}}}\')\n'
        "result = {'scalars': {'beta': 1.0}}"
    )

    assert outcome.status == "ok", outcome.error
    assert outcome.payload["scalars"]["beta"] == 1.0


async def test_a_caught_denial_still_marks_the_run() -> None:
    """Swallowing the refusal does not buy back the result.

    A run that reached for a socket is a run whose numbers nobody should read,
    whether or not the code recovered. `SandboxDenied` derives from
    `BaseException` so an ordinary `except Exception` cannot even see it, and
    the denial is recorded either way.
    """
    outcome = await _run(
        SMUGGLE + "s = smuggled('socket')\n"
        "try:\n"
        "    s.socket(s.AF_INET, s.SOCK_STREAM)\n"
        "except Exception:\n"
        "    pass\n"
        "result = {'scalars': {'beta': 1.0}}"
    )

    assert outcome.status == "denied"
    assert outcome.payload == {}
