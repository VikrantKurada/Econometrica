"""Operating-system resource caps for the sandbox child.

The only platform-specific file in the package, and it exists because
``resource`` is POSIX-only — verified absent on this machine
(``ModuleNotFoundError: No module named 'resource'``). Windows gets the same
guarantees from a Job Object, reached through ``ctypes`` rather than through a
new dependency: ``pywin32`` would be ~10 MB of bindings for four calls.

The two implementations differ in *when* they apply, which is why the guard has
two hooks rather than one:

* POSIX sets its limits in the child between ``fork`` and ``exec``, so the
  process is never once unconstrained.
* Windows cannot. ``AssignProcessToJobObject`` needs a process to exist first,
  and ``subprocess.Popen`` gives no way to start one suspended and resume it —
  it closes the thread handle. The window is closed instead by the child's
  first act being a blocking read of stdin: the parent assigns the job, *then*
  writes the payload, so the child has nothing to do until it is inside the
  job. See the design note.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from typing import Protocol

from econometrica.sandbox.policy import INTERPRETER_PROCESS_COUNT, SandboxLimits


class ResourceGuard(Protocol):
    """Caps applied to one sandbox child."""

    @property
    def preexec_fn(self) -> Callable[[], None] | None:
        """Run in the child before ``exec``; POSIX only."""

    def attach(self, pid_handle: int) -> None:
        """Bind an already-started process to the caps; Windows only."""

    def close(self) -> None:
        """Release the guard, killing anything still inside it."""


class _NullGuard:
    """No caps at all. Never selected in production — see `make_guard`."""

    @property
    def preexec_fn(self) -> Callable[[], None] | None:
        return None

    def attach(self, pid_handle: int) -> None:
        return None

    def close(self) -> None:
        return None


# --- POSIX ------------------------------------------------------------------


class PosixResourceGuard:
    """``setrlimit`` caps, applied between fork and exec.

    **Not verified live.** This project is developed on Windows and the design
    note records that openly; the tests for this class skip here rather than
    passing vacuously, so a green suite on this machine is not evidence about
    Linux.
    """

    def __init__(self, limits: SandboxLimits) -> None:
        self.limits = limits

    @property
    def preexec_fn(self) -> Callable[[], None] | None:
        limits = self.limits

        def apply() -> None:
            # Gated on the platform rather than merely imported inside the
            # function: `mypy` type-checks for the platform it runs on, and on
            # Windows every `resource` member is an attribute error. The guard
            # makes the block unreachable there instead of needing five
            # `type: ignore`s that would hide a real mistake on Linux.
            if sys.platform == "win32":  # pragma: no cover - see above
                return
            import resource

            memory = limits.memory_bytes
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            cpu = int(limits.cpu_seconds) + 1
            resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
            # Belt-and-braces beside the audit hook's write ban: a process that
            # cannot create a byte of file cannot write one however it got the
            # descriptor.
            resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))

        return apply

    def attach(self, pid_handle: int) -> None:
        return None

    def close(self) -> None:
        return None


# --- Windows ----------------------------------------------------------------

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

#: `PerProcessUserTimeLimit` counts in 100-nanosecond units.
_HUNDRED_NANOSECONDS_PER_SECOND = 10_000_000


if sys.platform == "win32":  # pragma: no branch - platform selection
    import ctypes.wintypes as wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = (
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        )

    class _BasicLimitInformation(ctypes.Structure):
        _fields_ = (
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            # ULONG_PTR, so it is pointer-width — `c_ulong` would be four bytes
            # on a 64-bit build and every field after it would be misread.
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        )

    class _ExtendedLimitInformation(ctypes.Structure):
        _fields_ = (
            ("BasicLimitInformation", _BasicLimitInformation),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        )


class JobObjectGuard:
    """A Windows Job Object holding memory, CPU and process-count caps.

    The job is unnamed and carries ``KILL_ON_JOB_CLOSE``, so closing the handle
    destroys whatever is still inside it. That is what makes a wall-clock
    timeout total: the parent does not have to find the descendants it never
    saw created.
    """

    def __init__(self, limits: SandboxLimits) -> None:
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle: int | None = None
        self._create(limits)

    @property
    def preexec_fn(self) -> Callable[[], None] | None:
        # `preexec_fn` is POSIX-only and `Popen` rejects it on Windows.
        return None

    def attach(self, pid_handle: int) -> None:
        if self._handle is None:
            raise RuntimeError("the job object has already been closed")
        if not self._kernel32.AssignProcessToJobObject(
            ctypes.wintypes.HANDLE(self._handle), ctypes.wintypes.HANDLE(pid_handle)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle is None:
            return
        self._kernel32.CloseHandle(ctypes.wintypes.HANDLE(self._handle))
        self._handle = None

    # --- internals ----------------------------------------------------------

    def _create(self, limits: SandboxLimits) -> None:
        kernel32 = self._kernel32
        kernel32.CreateJobObjectW.restype = ctypes.wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.wintypes.LPCWSTR]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = int(handle)

        info = _ExtendedLimitInformation()
        info.ProcessMemoryLimit = limits.memory_bytes
        info.JobMemoryLimit = limits.memory_bytes
        info.BasicLimitInformation.PerProcessUserTimeLimit = int(
            limits.cpu_seconds * _HUNDRED_NANOSECONDS_PER_SECOND
        )
        info.BasicLimitInformation.ActiveProcessLimit = INTERPRETER_PROCESS_COUNT
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_JOB_MEMORY
            | _JOB_OBJECT_LIMIT_PROCESS_TIME
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )

        if not kernel32.SetInformationJobObject(
            ctypes.wintypes.HANDLE(self._handle),
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = ctypes.get_last_error()
            self.close()
            raise ctypes.WinError(error)


def make_guard(limits: SandboxLimits) -> ResourceGuard:
    """The guard this platform can actually enforce.

    An unrecognised platform gets `_NullGuard` and a loud refusal rather than
    silent best-effort: a sandbox whose caps quietly do nothing is worse than
    one that will not start, because only the second is noticed.
    """
    if sys.platform == "win32":
        return JobObjectGuard(limits)
    if sys.platform.startswith(("linux", "darwin", "freebsd")):
        return PosixResourceGuard(limits)
    raise RuntimeError(
        f"the code sandbox has no resource caps for platform {sys.platform!r};"
        " refusing to run generated code uncapped"
    )
