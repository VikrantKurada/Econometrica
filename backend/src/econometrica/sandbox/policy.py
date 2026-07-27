"""What the sandbox permits — the whole policy, in one readable place.

Split out from the runner so that reviewing "what can generated code do" is
reading one short file rather than tracing an argument through a subprocess
launch. Everything here is data; `child.py` enforces it and `runner.py` never
looks at it.

The three layers this policy spans are not equally strong, and the design note
(`docs/plans/2026-07-27-econometrica-sandbox-design.md`) says so with the probe
output that proves it:

* `FORBIDDEN_EVENTS` and the `open` path rules are enforced by a PEP 578 audit
  hook. It fires from C, it cannot be unregistered, and it holds no matter how
  the code got hold of the callable — which is the layer that actually stops
  things.
* `ALLOWED_IMPORTS` is enforced by a gated ``__import__`` in the generated
  code's own builtins. It stops every ordinary route and is *bypassable* via
  ``().__class__.__base__.__subclasses__()``; a test performs that bypass and
  asserts the audit hook still refuses the call.
* `SandboxLimits` is enforced by the operating system — a Job Object on
  Windows, ``setrlimit`` on POSIX.
"""

from dataclasses import dataclass

#: Top-level modules generated code may import. Everything an econometric
#: calculation needs and nothing that reaches the machine: no `os`, no
#: `pathlib`, no `socket`, no `importlib`. Adding to this list is a security
#: decision, which is why `FORBIDDEN_IMPORTS` exists to make one class of
#: careless addition fail a test rather than a review.
ALLOWED_IMPORTS = frozenset(
    {
        # The computation itself.
        "numpy",
        "pandas",
        "scipy",
        "statsmodels",
        "arch",
        "linearmodels",
        # Ordinary pure-Python support.
        "math",
        "cmath",
        "statistics",
        "decimal",
        "fractions",
        "random",
        "itertools",
        "functools",
        "operator",
        "collections",
        "heapq",
        "bisect",
        "datetime",
        "json",
        "re",
        "string",
        "textwrap",
        "typing",
        "dataclasses",
        "enum",
        "abc",
        "copy",
        "warnings",
    }
)

#: Modules that must never appear in `ALLOWED_IMPORTS`. This list enforces
#: nothing at runtime — the allowlist already denies everything absent from it.
#: It exists so that adding `os` to the allowlist in a hurry fails a test
#: instead of passing a review, which is the realistic way this gate would be
#: widened by accident.
FORBIDDEN_IMPORTS = frozenset(
    {
        "os",
        "sys",
        "io",
        "builtins",
        "socket",
        "ssl",
        "select",
        "selectors",
        "asyncio",
        "subprocess",
        "multiprocessing",
        "threading",
        "signal",
        "ctypes",
        "mmap",
        "importlib",
        "imp",
        "runpy",
        "code",
        "codeop",
        "compileall",
        "pickle",
        "shelve",
        "marshal",
        "dbm",
        "sqlite3",
        "shutil",
        "pathlib",
        "glob",
        "fnmatch",
        "tempfile",
        "fileinput",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "poplib",
        "imaplib",
        "telnetlib",
        "webbrowser",
        "xmlrpc",
        "wsgiref",
        "platform",
        "sysconfig",
        "site",
        "gc",
        "inspect",
        "traceback",
        "atexit",
        "winreg",
        "msvcrt",
        "posix",
        "nt",
        "pty",
        "tty",
        "termios",
        # The application itself. Generated code reaching our settings, our
        # database session or `storage/keys.enc` would make every other
        # restriction beside the point.
        "econometrica",
    }
)

#: Imported before the lockdown, with full privileges, because they read files
#: and probe the CPU at import time and could never do it afterwards. Anything
#: they do here would have happened in the parent process anyway.
#:
#: `arch` is the reason lazy imports still have to work after lockdown: it
#: pulls in `pyarrow.pandas_compat` at *fit* time, not at import time.
PRELOAD_MODULES: tuple[str, ...] = (
    "numpy",
    "pandas",
    "scipy",
    "scipy.stats",
    "scipy.optimize",
    "statsmodels.api",
    "statsmodels.tsa.api",
    "statsmodels.tsa.stattools",
    "arch",
    "linearmodels",
)

#: Audit events refused outright. Each is raised from CPython's own C code, so
#: reaching the callable by any route — including the `__subclasses__` bypass —
#: still ends here.
#:
#: `open`, `os.listdir` and `os.scandir` are absent deliberately: they are
#: refused *conditionally*, by path, because the allowlisted libraries read
#: their own files off disk at runtime and blocking that outright makes a GARCH
#: fit impossible.
FORBIDDEN_EVENTS = frozenset(
    {
        # Network, in every spelling the standard library offers.
        "socket.__new__",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyaddr",
        "socket.sendto",
        "socket.sethostname",
        # Another process, in every spelling.
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.fork",
        "os.forkpty",
        "os.spawn",
        "os.startfile",
        "pty.spawn",
        # Changing the filesystem. Writes are caught by the `open` rule; these
        # are the mutations that never open anything.
        "os.remove",
        "os.rename",
        "os.link",
        "os.symlink",
        "os.mkdir",
        "os.rmdir",
        "os.truncate",
        "os.chmod",
        "os.chown",
        "os.putenv",
        "os.unsetenv",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.move",
        "shutil.unpack_archive",
        # Reaching outside the interpreter entirely.
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ctypes.dlsym/handle",
        "ctypes.call_function",
        "ctypes.cdata",
        "ctypes.set_exception",
        "winreg.OpenKey",
        "winreg.CreateKey",
        "winreg.SetValue",
        "winreg.QueryValue",
        "cpython.run_file",
        "cpython.run_module",
    }
)

#: Modes that mean a file is being opened for anything other than reading. `+`
#: is here because `r+` writes.
WRITE_MODE_CHARACTERS = frozenset("wax+")


@dataclass(frozen=True)
class SandboxLimits:
    """The caps the operating system enforces.

    Defaults chosen from the probes in the design note, not from taste:

    * **512 MB** is roughly double what numpy, pandas, scipy, statsmodels and
      arch need together once BLAS is pinned to one thread. Unpinned on a
      24-CPU machine, OpenBLAS reserves so much that `import numpy` dies
      reproducibly inside a *1 GB* cap — which is why `runner.py` sets the
      thread count rather than leaving it to the environment.
    * **20 s wall** is the real timeout. It is the parent's, not the job's.
    * **60 s CPU** is a backstop and nothing more. Windows accounts job CPU
      time so coarsely that a 1 s cap was measured firing at 5.9 s, 7.4 s and
      8.1 s across three runs, so it can never be the thing a caller relies on.
    """

    memory_bytes: int = 512 * 1024 * 1024
    wall_seconds: float = 20.0
    cpu_seconds: float = 60.0
    #: Longest envelope the child may return. A result is a handful of
    #: estimates, so anything approaching this is a mistake rather than a large
    #: answer.
    max_output_bytes: int = 4 * 1024 * 1024


#: The `ActiveProcessLimit` a Job Object is given. Not 1: under `uv`,
#: `sys.executable` is a 45 KB trampoline that spawns the real interpreter, so
#: a limit of 1 refuses the sandbox its own interpreter — measured, with
#: `os error 1816`. Two lets the sandbox start and still refuses it a
#: grandchild at the kernel, underneath the `subprocess.Popen` audit event.
INTERPRETER_PROCESS_COUNT = 2

#: Marks the line on stdout carrying the child's JSON envelope. Generated code
#: may print, and its output is captured separately — but a print that reached
#: fd 1 directly must not be mistaken for the result.
ENVELOPE_PREFIX = "__ECONOMETRICA_SANDBOX__"
