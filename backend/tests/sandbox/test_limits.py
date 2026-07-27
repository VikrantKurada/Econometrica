"""The operating system's own caps, tested without the audit hook in the way.

`test_escapes.py` proves the *in-process* layer refuses a spawn. That layer is
Python, and Python layers are bypassable in principle. These tests drive the
kernel layer directly — a plain interpreter inside the same guard the sandbox
uses, with no hook installed — so the two claims stay independent.
"""

import ctypes
import subprocess
import sys

import pytest

from econometrica.sandbox.limits import PosixResourceGuard, make_guard
from econometrica.sandbox.policy import INTERPRETER_PROCESS_COUNT, SandboxLimits

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Job Objects are Windows-only")
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="setrlimit is POSIX-only")


def _run_in_guard(source: str, limits: SandboxLimits) -> subprocess.CompletedProcess[str]:
    guard = make_guard(limits)
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", source],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if sys.platform == "win32":
            guard.attach(int(process._handle))  # type: ignore[attr-defined]
        out, err = process.communicate("go", timeout=60)
    finally:
        guard.close()
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


@windows_only
def test_the_job_object_refuses_a_grandchild_with_no_hook_installed() -> None:
    """The kernel layer, on its own.

    Nothing here installs an audit hook, so the only thing that can refuse the
    spawn is `ActiveProcessLimit`. The assertion names the Windows error
    because a bare "it failed" would also pass if `sys.executable` were simply
    missing.
    """
    done = _run_in_guard(
        "import subprocess, sys\n"
        "sys.stdin.read(2)\n"
        "try:\n"
        "    subprocess.run([sys.executable, '-c', 'print(1)'], capture_output=True, timeout=30)\n"
        "    print('SPAWNED')\n"
        "except OSError as exc:\n"
        "    print('REFUSED', exc.winerror)\n",
        SandboxLimits(),
    )

    assert "SPAWNED" not in done.stdout
    # 1816 is ERROR_NOT_ENOUGH_QUOTA — the job's process limit, not a missing
    # interpreter or a path problem.
    assert "REFUSED 1816" in done.stdout, done.stdout + done.stderr


@windows_only
def test_the_interpreter_itself_starts_within_the_process_limit() -> None:
    """Why the limit is two rather than one.

    Under `uv`, `sys.executable` is a 45 KB trampoline that spawns the real
    interpreter, so a limit of one refuses the sandbox its own Python. This
    asserts the constant is large enough to be usable and small enough to still
    be a limit.
    """
    done = _run_in_guard("import sys; sys.stdin.read(2); print('STARTED')", SandboxLimits())

    assert done.stdout.strip() == "STARTED", done.stderr
    assert INTERPRETER_PROCESS_COUNT == 2


@windows_only
def test_the_job_object_caps_memory() -> None:
    done = _run_in_guard(
        "import sys\n"
        "sys.stdin.read(2)\n"
        "try:\n"
        "    blocks = [bytearray(1024 * 1024) for _ in range(600)]\n"
        "    print('ALLOCATED')\n"
        "except MemoryError:\n"
        "    print('CAPPED')\n",
        SandboxLimits(memory_bytes=128 * 1024 * 1024),
    )

    assert "CAPPED" in done.stdout, done.stdout + done.stderr


@windows_only
def test_closing_the_guard_twice_is_harmless() -> None:
    guard = make_guard(SandboxLimits())
    guard.close()
    guard.close()

    with pytest.raises(RuntimeError, match="already been closed"):
        guard.attach(0)


@windows_only
def test_attaching_a_bad_handle_raises_rather_than_running_uncapped() -> None:
    """A guard that silently fails to attach is worse than no guard.

    It would look exactly like a working sandbox while capping nothing, so the
    failure has to be loud enough that `execute` never reaches the payload.
    """
    guard = make_guard(SandboxLimits())
    try:
        with pytest.raises(OSError):
            guard.attach(0xDEAD)
    finally:
        guard.close()


@posix_only
def test_the_posix_guard_sets_rlimits() -> None:
    guard = PosixResourceGuard(SandboxLimits(memory_bytes=256 * 1024 * 1024, cpu_seconds=5))
    apply = guard.preexec_fn
    assert apply is not None

    import resource

    original = (
        resource.getrlimit(resource.RLIMIT_AS),
        resource.getrlimit(resource.RLIMIT_CPU),
        resource.getrlimit(resource.RLIMIT_FSIZE),
    )
    pid = __import__("os").fork()
    if pid == 0:  # pragma: no cover - the child never returns to pytest
        apply()
        ok = (
            resource.getrlimit(resource.RLIMIT_AS)[0] == 256 * 1024 * 1024
            and resource.getrlimit(resource.RLIMIT_FSIZE) == (0, 0)
        )
        __import__("os")._exit(0 if ok else 1)
    _, status = __import__("os").waitpid(pid, 0)
    assert status == 0
    assert (
        resource.getrlimit(resource.RLIMIT_AS),
        resource.getrlimit(resource.RLIMIT_CPU),
        resource.getrlimit(resource.RLIMIT_FSIZE),
    ) == original


def test_an_unsupported_platform_refuses_rather_than_running_uncapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sandbox whose caps quietly do nothing is worse than one that will not
    start, because only the second is noticed."""
    monkeypatch.setattr(sys, "platform", "sunos5")

    with pytest.raises(RuntimeError, match="refusing to run generated code uncapped"):
        make_guard(SandboxLimits())


@windows_only
def test_ctypes_structures_are_pointer_width() -> None:
    """`Affinity` is a ULONG_PTR.

    Declaring it `c_ulong` is four bytes on a 64-bit build, and every field
    after it — including `ActiveProcessLimit` and both memory limits — would be
    read from the wrong offset. The job would then be configured with garbage
    and `SetInformationJobObject` might still succeed.
    """
    from econometrica.sandbox.limits import _BasicLimitInformation, _ExtendedLimitInformation

    assert _BasicLimitInformation.Affinity.size == ctypes.sizeof(ctypes.c_size_t)
    # 64-bit layout: 64 bytes of basic limits, 48 of IO_COUNTERS, four SIZE_T.
    # With `Affinity` declared `c_ulong` the first number is 56 and every
    # offset after it slips.
    assert ctypes.sizeof(_BasicLimitInformation) == 64
    assert ctypes.sizeof(_ExtendedLimitInformation) == 144
    assert _ExtendedLimitInformation.ProcessMemoryLimit.offset == 112
