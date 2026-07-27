# Task 6.15 — the sandboxed code escape hatch: design note

> Written before implementation, as `docs/plans/2026-07-27-econometrica-phase-6.md`
> Task 6.15 requires. Every claim below was probed against the real interpreter
> on this machine (Windows 11, CPython 3.12.12, 24 logical CPUs) on 2026-07-27,
> not reasoned about.

---

## What this is for, and what it is not

§2 of the design chose **registry-first with a gated code escape hatch**. The
registry answers the canonical majority; when nothing in it fits, a Quant Coder
writes code that a locked-down subprocess executes. The output is marked as an
**unvalidated method** everywhere it surfaces, the Validator must sign off, and
the whole path is off by default and project-scoped.

The invariant does not bend here, it is *fenced*. "LLMs never compute
statistics" means every number a user sees traces to a tested function with a
manifest. A sandbox result has no tested function behind it, so it must never
be indistinguishable from one. That is what the marking is for, and it is the
single most important thing this task ships — more important than any
individual restriction.

---

## The threat model, stated honestly

Three attackers, and they are not equally plausible:

1. **A model that writes wrong or careless code.** Overwhelmingly the likely
   case: an infinite loop, a 40 GB allocation, a `pd.read_csv("prices.csv")`
   against a file that is not there. Containment here is about not taking the
   application down.
2. **A model that writes code doing something it was not asked to** — reading
   the user's files, phoning home, shelling out. Not malice, but a model that
   read a prompt-injected document and complied.
3. **A deliberate attacker with control of the generated code.** They would
   need control of the model or of a document the model read.

**A pure-Python in-process sandbox cannot stop (3), and this design does not
claim to.** The probe proves it in one line: with a fully gated `__import__`,
`().__class__.__base__.__subclasses__()` still hands back
`<class 'subprocess.Popen'>`. Any design pretending otherwise is lying.

So the layers are ranked by what they actually hold:

| Layer | Stops | Strength |
|---|---|---|
| Separate process + Job Object / rlimits | resource exhaustion, runaway processes | **kernel-enforced** — a real boundary |
| PEP 578 audit hook on operations | network, filesystem, process spawn, `ctypes` | **strong** — fires from C, cannot be unregistered |
| Gated `__import__` in the user's builtins | accidental and obvious use of forbidden modules | **weak alone** — bypassable, and tested as such |

The import allowlist is a clarity control. The audit hook is the security
control for what the code *does*. The process boundary is the security control
for what it *consumes*. Every escape test is written against the layer that
actually holds, and the import allowlist's escape test asserts the bypass
**reaches** `Popen` and that **calling it still fails**.

---

## What the probes found

### `resource` does not exist on Windows

```
resource: ABSENT (No module named 'resource')
```

Confirmed, as the phase plan warned. Caps come from a Job Object.

### A Job Object memory cap works, and fails *gracefully*

A child allocating 400 MB under a 128 MB `ProcessMemoryLimit` +
`JobMemoryLimit` does not get killed — it gets a `MemoryError` at the
allocation, exit code 1, in 0.09 s. A child allocating 32 MB under the same cap
succeeds. That is better than a kill: the failure is attributable, and the
traceback names the line.

### `PerProcessUserTimeLimit` fires, but far too late to be a timeout

A busy loop under a **1 second** CPU cap was killed with
`0xC0000044` (`STATUS_QUOTA_EXCEEDED`) after **5.9 s, 7.4 s and 8.1 s** across
three runs. Windows accounts job CPU time coarsely.

**Consequence:** the CPU cap is a backstop, not the timeout. The wall-clock cap
is enforced by the parent — `communicate(timeout=…)`, then kill the job, which
takes the whole process tree with it via
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. A test asserts the wall-clock cap holds
to within a second; nothing asserts the CPU cap's timing, because it does not
have any.

### `ActiveProcessLimit` blocks spawning at the kernel — and 1 is too few

With `ActiveProcessLimit = 1` the sandbox child never started:

```
error: uv trampoline failed to spawn Python child process
  Caused by: uncategorized error (os error 1816)
```

**`sys.executable` under `uv` is a 45 KB trampoline** (`.venv/Scripts/python.exe`)
that spawns the real interpreter at
`C:\Users\…\uv\python\cpython-3.12-windows-x86_64-none\python.exe`. So the
sandbox costs two processes before it runs a line.

With `ActiveProcessLimit = 2` the child runs and a grandchild is refused by the
kernel:

```
CHILD RUNNING
SPAWN BLOCKED OSError [WinError 1816] Not enough quota is available
```

That is a second, independent block on process creation, underneath the
`subprocess.Popen` audit event. The limit is expressed as
`processes = 1 + interpreter_launch_overhead` rather than as a bare `2`, so the
reason survives the next time somebody wonders why it is not 1.

### OpenBLAS reserves more than 1 GB unless it is pinned to one thread

This is the finding that would have cost a day. With default thread settings on
a 24-CPU machine, **`import numpy` inside a 1 GB job cap dies reproducibly**:

```
OpenBLAS error: Memory allocation still failed after 10 retries, giving up.
```

Twice out of two. With `OPENBLAS_NUM_THREADS=1` and `OMP_NUM_THREADS=1` the
whole stack — numpy, pandas, scipy, statsmodels, arch — imports and runs inside
**256 MB**.

So the child's environment pins BLAS to a single thread, and that is not only a
memory workaround: a CPU cap over 24 threads means the cap is reached in
1/24th of the wall time a reader would expect, and a single-threaded BLAS makes
the numbers reproducible run to run. The default cap is 512 MB, which the probe
shows is roughly double what the stack needs.

### Every audit event that matters fires

Under `sys.addaudithook`, on 3.12 / Windows: `socket.__new__`,
`socket.connect`, `socket.getaddrinfo`, `open`, `os.system`,
`subprocess.Popen`, `os.remove`, `os.rename`, `ctypes.dlopen` — all fire, all
from C, and an audit hook **cannot be unregistered**. `urllib.request.urlopen`
fails through them without needing its own case.

### Blocking `open` outright breaks the allowlisted libraries

`arch.arch_model(...).fit()` lazily imports `pyarrow.pandas_compat` **at fit
time**, long after preloading. With `open` blocked unconditionally it raises
`PermissionError` and the sandbox can never run a GARCH.

The rule that works, verified: **all writes denied; reads permitted only under
`sys.prefix` and `sys.base_prefix`.** Under it, `arch` fits (`omega 1.066673`),
`statsmodels` runs, reading `C:/Windows/win.ini` is denied, and opening any
file for writing is denied. Those two roots hold the interpreter and its
site-packages and nothing else — `storage/`, which holds `keys.enc`, is outside
both.

### The `import` audit event is useless as an allowlist

`import socket` after `pandas` is loaded **is not blocked** by an audit hook
that denies the `import` event — because `_find_and_load` never runs on a
`sys.modules` cache hit, so no event is raised. `import csv` behaves the same.
A design that put the allowlist there would have shipped a gate with a hole in
it that every real dependency graph opens.

The allowlist therefore lives in a **gated `__import__` installed in the user
code's own `__builtins__`**. Probed: `import socket`, `import os`,
`__import__('os')`, `importlib.import_module('os')` and `sys.modules['os']` are
all refused, and `import numpy` works. And the bypass is refused nothing —
`().__class__.__base__.__subclasses__()` still finds `Popen`, which is exactly
why the audit hook exists and why that route is a test rather than a comment.

---

## The shape

```
sandbox/
  policy.py   the allowlist, the denied roots, the read roots, SandboxLimits
  limits.py   Job Object (Windows) / setrlimit (POSIX) — the only OS-specific file
  child.py    the child entry point: preload, lock down, exec, emit JSON
  runner.py   spawn, apply limits, feed the payload, collect, classify
```

`agents/quant_coder.py` sits above it: an `Agent` whose output is a `CodeDraft`,
executed through `runner.run_sandboxed`, and turned into a `ResultSet` that is
marked.

### Why the child is a module, not a `-c` string

The child is launched as `python -I -m econometrica.sandbox.child`. `-I`
(isolated) drops `PYTHON*` environment variables and the script directory from
`sys.path`; the venv's own `pyvenv.cfg` still supplies site-packages, verified.
A `-c` blob would be unreadable, untestable in isolation, and impossible to set
a breakpoint in.

### Why the payload arrives on stdin

`AssignProcessToJobObject` happens **after** `CreateProcess`, so there is a
window in which the child is unconstrained. `subprocess.Popen` cannot create a
suspended process and resume it — it closes the thread handle — so the window
cannot be closed that way.

It is closed by making the child's first act a blocking `sys.stdin.buffer.read()`.
The parent assigns the job, *then* writes the payload. The child does nothing
before it is inside the job because it has nothing to do. Payload delivery is
the go-signal, and there is no second mechanism to keep in sync.

### The contract with the generated code

The payload is JSON: the code, the frame as `{columns, index, data}`, and the
limits. The child binds `frame` (a `pd.DataFrame`) into the execution globals
and the code must assign a dict named `result`, whose keys are the `ResultSet`
fields it may populate — `estimates`, `scalars`, `diagnostics`, `tables`,
`series`. Anything else is rejected by the parent, which validates the payload
into a real `ResultSet` on this side of the boundary. **The child never
constructs a `ResultSet`**; it emits JSON, and the trusted side decides whether
that JSON is one.

### Classifying an exit

`SandboxOutcome.status` is one of `ok`, `denied`, `failed`, `timeout`,
`out_of_memory`. `denied` is its own status rather than a kind of `failed`
because "the code tried to open a socket" and "the code divided by zero" are
different facts about a run, and the first one belongs in the trace where
somebody will read it.

---

## The restrictions, and the escape attempt each one gets

The plan asks for one per restriction. These are the tests.

| Restriction | Escape attempt | Must fail with |
|---|---|---|
| No network | `socket.socket(...)`, and `urllib.request.urlopen` | `denied`, naming `socket.__new__` |
| No DNS | `socket.getaddrinfo("example.com", 80)` | `denied` |
| No filesystem writes | `open("escape.txt", "w")`, `os.remove`, `os.rename` | `denied`; and the file does not exist afterwards |
| No filesystem reads outside the runtime | `open("C:/Windows/win.ini")`, and the project's own `storage/` | `denied` |
| No process spawn | `subprocess.run`, `os.system` | `denied` |
| No process spawn, kernel layer | `Popen` reached via `__subclasses__`, bypassing the import gate | `denied` — the bypass **succeeds** and the call still fails |
| Import allowlist | `import socket`, `import os`, `__import__`, `importlib.import_module` | `denied`, naming the module |
| No `ctypes` | `ctypes.CDLL("kernel32")` | `denied` |
| Wall-clock cap | `while True: pass` | `timeout`, within a second of the cap |
| Memory cap | allocate 4× the cap | `out_of_memory` |
| No reaching the app | `import econometrica` | `denied` |
| Off by default | run with the capability unresolved | refuses before spawning anything |

Two of these are worth their own note. The **kernel-layer spawn test** is the
one that proves the layering claim above rather than restating it: it
deliberately defeats the import allowlist and asserts the audit hook still
holds. And the **filesystem-write test asserts the absence of the file**, not
just the exception — an exception says the call raised, and only the missing
file says nothing was written.

---

## Gating

Three conditions, all of which must hold before a single byte of generated code
is executed:

1. **`resolve_capabilities(project, chat).code_sandbox` is true.** Already
   project-scoped only, already default-false, already refuses a chat override
   — Phase 1 built this and it needs no change.
2. **The validation tier is not `single`.** §2 says the Validator must sign
   off, and `single` skips the Validator. A sandbox result in that tier would
   be an unreviewed number produced by a model, which is the exact thing the
   whole design exists to prevent. The orchestrator refuses the run rather than
   silently dropping the code step.
3. **The result is marked.** A `ResultSet` from the sandbox carries
   `tool="sandbox:<slug>"` and a manifest whose `tool_version` is
   `unvalidated`. The run carries an `unvalidated_method` flag rendered exactly
   like `synthetic_data` — an alert in the run banner that no tab can hide, and
   a line in the print-only `Provenance`.

Condition 3 is the one that must never regress, so it is asserted the same way
the synthetic flag is: at the seam, with a test per surface.

---

## What this task deliberately does not do

**It does not let the Planner reach for code on its own initiative.** A plan
gains an optional, default-empty `code_steps`, and the Planner is told about it
only when the capability is on — so every existing plan, prompt and test is
unchanged, and a project that never turns the sandbox on cannot get a code step
by accident.

**It does not sandbox the imports of the libraries themselves.** numpy, pandas,
scipy, statsmodels and arch are preloaded with full privileges before lockdown,
because they read files and probe the CPU at import time. Anything they did
would have happened in the parent anyway.

**It does not run untrusted code from a user.** The only code that reaches the
runner is code a model wrote in this process, in response to a plan the user
saw. That is a narrower problem than a public code-execution service, and it is
why (3) in the threat model is out of scope rather than unaddressed.

---

## POSIX

`limits.py` implements `setrlimit` for `RLIMIT_AS`, `RLIMIT_CPU`, `RLIMIT_FSIZE`
and `RLIMIT_NPROC` through `preexec_fn`, which is the direct equivalent of the
Job Object. **It is not verified live — this machine is Windows.** The tests for
it skip on Windows rather than passing vacuously, and this note is the record
that the POSIX path is written-but-unprobed, so nobody reads a green suite here
as evidence about Linux.
