"""Running generated code in a process that cannot do much.

The trusted side of the boundary. It spawns the child, applies the operating
system's caps to it, feeds it a payload, and classifies what comes back. It
never executes a line of the generated code itself, and it never trusts the
child's envelope beyond reading it as JSON — a `ResultSet` is built here, from
validated fields, or not at all.

Read `docs/plans/2026-07-27-econometrica-sandbox-design.md` before changing
anything in this package. It carries the probe output every constant is derived
from, and the threat model that says which of the three layers is actually
load-bearing.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from econometrica.sandbox.limits import make_guard
from econometrica.sandbox.policy import (
    ALLOWED_IMPORTS,
    ENVELOPE_PREFIX,
    FORBIDDEN_EVENTS,
    PRELOAD_MODULES,
    WRITE_MODE_CHARACTERS,
    SandboxLimits,
)

SandboxStatus = Literal["ok", "denied", "failed", "timeout", "out_of_memory"]

_CHILD = Path(__file__).with_name("child.py")

#: Windows kills a process that exceeds a Job Object CPU cap with
#: ``STATUS_QUOTA_EXCEEDED``. Named because ``-1073741756`` in a log tells
#: nobody anything.
_STATUS_QUOTA_EXCEEDED = 0xC0000044

#: Pinning BLAS to one thread is not a preference. On a 24-CPU machine
#: OpenBLAS reserves so much address space that ``import numpy`` dies
#: reproducibly inside a 1 GB job cap; pinned, the whole stack fits in 256 MB.
#: It also makes a CPU cap mean what a reader expects, and makes results
#: reproducible run to run.
_SINGLE_THREADED_BLAS = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


@dataclass(frozen=True)
class SandboxOutcome:
    """What became of one execution.

    `denied` is its own status rather than a flavour of `failed` because "the
    code tried to open a socket" and "the code divided by zero" are different
    facts about a run, and only the first belongs in a security conversation.
    """

    status: SandboxStatus
    payload: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    error: str = ""
    #: Every restriction the code ran into, in order, even when it caught the
    #: refusal and carried on.
    denials: tuple[str, ...] = ()
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"


async def run_sandboxed(
    code: str,
    frame: pd.DataFrame,
    *,
    limits: SandboxLimits | None = None,
) -> SandboxOutcome:
    """Execute `code` against `frame` in an isolated process.

    Synchronous underneath and wrapped in a thread, for the same reason the
    yfinance adapter is: the impedance mismatch is one call wide and
    reimplementing `subprocess` asynchronously would buy nothing.
    """
    return await asyncio.to_thread(execute, code, frame, limits or SandboxLimits())


def execute(code: str, frame: pd.DataFrame, limits: SandboxLimits) -> SandboxOutcome:
    payload = json.dumps(
        {
            "code": code,
            "frame": _describe(frame),
            "preload": list(PRELOAD_MODULES),
            "allowed_imports": sorted(ALLOWED_IMPORTS),
            "forbidden_events": sorted(FORBIDDEN_EVENTS),
            "write_mode_characters": "".join(sorted(WRITE_MODE_CHARACTERS)),
        }
    ).encode("utf-8")

    guard = make_guard(limits)
    started = time.perf_counter()
    try:
        # The executable is this interpreter and the argument is a file in
        # this package: nothing here comes from the generated code.
        process = subprocess.Popen(
            [sys.executable, "-I", str(_CHILD)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_child_env(),
            **_posix_only(guard),
        )
        # Windows can only cap a process that exists, so this is where the job
        # is applied — before the payload is written, which is the only thing
        # the child is waiting for.
        if sys.platform == "win32":
            guard.attach(int(process._handle))  # type: ignore[attr-defined]

        try:
            out, err = process.communicate(payload, timeout=limits.wall_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            out, err = process.communicate()
            return SandboxOutcome(
                status="timeout",
                error=f"the code did not finish within {limits.wall_seconds:g}s",
                stdout=_text(out, limits),
                duration_ms=(time.perf_counter() - started) * 1000,
            )
    finally:
        # Closing the job kills anything still inside it, which is what makes a
        # timeout total: descendants the parent never saw created die too.
        guard.close()

    return _classify(
        returncode=process.returncode,
        out=_text(out, limits),
        err=_text(err, limits),
        duration_ms=(time.perf_counter() - started) * 1000,
    )


# --- internals --------------------------------------------------------------


def _posix_only(guard: Any) -> dict[str, Any]:
    """`preexec_fn` is POSIX-only and `Popen` rejects it on Windows."""
    fn = guard.preexec_fn
    return {"preexec_fn": fn} if fn is not None else {}


def _child_env() -> dict[str, str]:
    """A minimal environment.

    Everything `PYTHON*` is dropped — ``-I`` ignores most of it, but not
    ``PYTHONWARNINGS`` or a stray ``PYTHONSTARTUP``, and passing them through
    would let the parent's shell configure the sandbox.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTHON") and key.upper() not in {"ECONOMETRICA_SECRET_KEY"}
    }
    env.update(_SINGLE_THREADED_BLAS)
    return env


def _describe(frame: pd.DataFrame) -> dict[str, Any]:
    """The frame as plain JSON.

    Not pickle. Unpickling is arbitrary code execution, and using it to set up
    a sandbox would be an odd way to begin.
    """
    index: list[Any]
    if isinstance(frame.index, pd.DatetimeIndex):
        index = [stamp.isoformat() for stamp in frame.index]
    else:
        index = [str(label) for label in frame.index]
    return {
        "columns": [str(column) for column in frame.columns],
        "index": index,
        "data": [[None if pd.isna(cell) else float(cell) for cell in row] for row in frame.values],
    }


def _text(raw: bytes, limits: SandboxLimits) -> str:
    if len(raw) > limits.max_output_bytes:
        raw = raw[: limits.max_output_bytes]
    return raw.decode("utf-8", errors="replace")


def _classify(*, returncode: int, out: str, err: str, duration_ms: float) -> SandboxOutcome:
    envelope = _envelope(out)
    if envelope is None:
        # No envelope means the child never reached its own error handling —
        # it was killed, or it died before it could speak.
        return SandboxOutcome(
            status=_status_from_exit(returncode),
            error=_exit_reason(returncode, err),
            stdout=out,
            duration_ms=duration_ms,
        )

    status = str(envelope.get("status", "failed"))
    result = envelope.get("result")
    return SandboxOutcome(
        status=status if status in {"ok", "denied", "failed", "out_of_memory"} else "failed",  # type: ignore[arg-type]
        payload=result if isinstance(result, dict) else {},
        stdout=str(envelope.get("stdout", "")),
        error=str(envelope.get("error", "")),
        denials=tuple(str(item) for item in envelope.get("denials", ())),
        duration_ms=duration_ms,
    )


def _envelope(out: str) -> dict[str, Any] | None:
    """The child's JSON, from the line that says it is the child's JSON.

    Generated code may print, and its output is captured separately — but a
    print that reached fd 1 directly must not be mistaken for a result, so the
    marker is required and the *last* marked line wins.
    """
    for line in reversed(out.splitlines()):
        if line.startswith(ENVELOPE_PREFIX):
            try:
                parsed = json.loads(line[len(ENVELOPE_PREFIX) :])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _status_from_exit(returncode: int) -> SandboxStatus:
    # Windows reports NTSTATUS codes as large negatives through `returncode`.
    if (returncode & 0xFFFFFFFF) == _STATUS_QUOTA_EXCEEDED:
        return "timeout"
    return "failed"


def _exit_reason(returncode: int, err: str) -> str:
    if (returncode & 0xFFFFFFFF) == _STATUS_QUOTA_EXCEEDED:
        return "the code exceeded the sandbox CPU cap"
    tail = err.strip().splitlines()[-3:]
    detail = " ".join(tail) if tail else "no output"
    return f"the sandbox process exited with {returncode}: {detail}"
