"""The sandbox child. **Everything in this file runs beside generated code.**

Launched by `runner.py` as ``python -I <this file>`` — by *path*, not as
``-m econometrica.sandbox.child``, and that is deliberate. ``-I`` keeps the
script's own directory off ``sys.path``, so nothing in this package is
importable here and the `econometrica` package never enters the sandbox
process at all. The policy therefore arrives in the payload rather than being
imported, which keeps `policy.py` the single source of truth without giving
generated code a route to the application's settings, database session or
``storage/keys.enc``.

The order of operations is the whole design and it is not rearrangeable:

1. **Read stdin first.** The parent assigns the Job Object *after* spawning,
   so there is a window in which this process is uncapped. Blocking on stdin
   before doing anything means the window contains no work. The payload
   arriving is the go-signal.
2. **Preload with full privileges.** numpy and friends read files and probe the
   CPU as they import; none of that could happen after step 3.
3. **Lock down**, with an audit hook that cannot be unregistered.
4. **Execute**, with a gated ``__import__`` in the generated code's builtins.
5. **Emit** one JSON envelope on a marked line of stdout.

Nothing here constructs a `ResultSet`. It emits JSON and the trusted side
decides whether that JSON is one.
"""

from __future__ import annotations

import io
import json
import os
import sys
import traceback
from typing import Any


class SandboxDenied(BaseException):
    """A restriction refused something.

    Derived from ``BaseException`` rather than ``Exception`` on purpose:
    generated code wrapping its escape attempt in ``except Exception`` must not
    be able to swallow the refusal and carry on as though nothing was tried.
    """


#: Every refusal, in order. Kept even when generated code catches one, because
#: a run that *attempted* a socket is a fact about that run whether or not it
#: recovered — and the parent turns any entry here into a `denied` outcome.
DENIALS: list[str] = []


def main() -> int:
    # Blocks until the parent has applied the resource caps. See the docstring.
    raw = sys.stdin.buffer.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # a malformed payload is our bug, not the code's
        _emit({"status": "failed", "error": f"unreadable payload: {exc}", "denials": []})
        return 1

    _emit(_execute(payload))
    return 0


# --- the run ----------------------------------------------------------------


def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    allowed_imports = frozenset(payload.get("allowed_imports", ()))
    forbidden_events = frozenset(payload.get("forbidden_events", ()))
    write_modes = frozenset(payload.get("write_mode_characters", "wax+"))

    try:
        modules = _preload(payload.get("preload", ()))
        frame = _build_frame(payload.get("frame", {}))
    except BaseException as exc:
        return {
            "status": "failed",
            "error": f"the sandbox could not start: {type(exc).__name__}: {exc}",
            "denials": [],
        }

    _install_audit_hook(forbidden_events, write_modes)
    namespace = _namespace(modules, allowed_imports, frame)

    captured = io.StringIO()
    real_stdout, sys.stdout = sys.stdout, captured
    status = "ok"
    error = ""
    result: Any = None
    try:
        # Executing model-written code is this module's entire purpose; the
        # restrictions above are what make it a defensible thing to do.
        exec(payload.get("code", ""), namespace)
        result = namespace.get("result")
    except SandboxDenied as exc:
        status, error = "denied", str(exc)
    except MemoryError:
        # Distinguished from an ordinary failure because it is a statement
        # about the cap rather than about the code's logic.
        status, error = "out_of_memory", "the code exceeded the sandbox memory cap"
    except BaseException as exc:
        status = "failed"
        error = _describe_failure(exc)
    finally:
        sys.stdout = real_stdout

    if status == "ok" and not isinstance(result, dict):
        # An empty answer reported as success is the worst outcome available:
        # the Quant Coder would publish a `ResultSet` with nothing in it and
        # the Validator would be asked to sign off on silence.
        status = "failed"
        error = (
            "the code did not assign a dict named `result`;"
            f" it assigned {type(result).__name__}"
        )

    if DENIALS and status == "ok":
        # Generated code can catch a `SandboxDenied` only by naming it, but a
        # bare `except BaseException` would still do it. A run that reached for
        # a socket does not get to report numbers regardless.
        status = "denied"
        error = DENIALS[0]

    if status == "ok":
        try:
            result = _coerce(result)
        except BaseException as exc:
            status = "failed"
            error = f"the result could not be serialised: {type(exc).__name__}: {exc}"

    return {
        "status": status,
        "result": result if status == "ok" else None,
        "error": error,
        "stdout": captured.getvalue(),
        "denials": list(DENIALS),
    }


def _preload(names: object) -> dict[str, Any]:
    """Import the permitted libraries while imports are still free."""
    import importlib

    loaded: dict[str, Any] = {}
    for name in names if isinstance(names, list | tuple) else ():
        module = importlib.import_module(str(name))
        loaded[str(name).split(".")[0]] = sys.modules[str(name).split(".")[0]]
        loaded.setdefault(str(name), module)
    return loaded


def _build_frame(spec: dict[str, Any]) -> Any:
    """The dataset, rebuilt from the parent's plain-JSON description.

    Deliberately not pickle: unpickling is arbitrary code execution, and doing
    it *inside* the sandbox to set the sandbox up would be an odd way to start.
    """
    import pandas as pd

    columns = list(spec.get("columns", []))
    index = list(spec.get("index", []))
    data = list(spec.get("data", []))
    frame = pd.DataFrame(data, columns=columns)
    if index:
        parsed = pd.to_datetime(pd.Series(index), errors="coerce", format="ISO8601")
        # A non-date index (an integer range, say) stays what it was rather
        # than becoming NaT — a silently null index would break every join the
        # generated code tried and look like the code's fault.
        frame.index = pd.DatetimeIndex(parsed) if parsed.notna().all() else pd.Index(index)
    return frame


# --- the lockdown -----------------------------------------------------------


def _install_audit_hook(forbidden_events: frozenset[str], write_modes: frozenset[str]) -> None:
    """The layer that actually holds.

    A PEP 578 hook fires from CPython's own C code and cannot be unregistered,
    so it refuses an operation however the callable was obtained — including
    via ``().__class__.__base__.__subclasses__()``, which defeats the import
    gate and is a test rather than a footnote.
    """
    read_roots = tuple(
        os.path.normcase(os.path.abspath(root))
        for root in (sys.prefix, sys.base_prefix)
        if root
    )

    def deny(reason: str) -> SandboxDenied:
        DENIALS.append(reason)
        return SandboxDenied(reason)

    def within_runtime(path: object) -> bool:
        # An `int` is a file descriptor and a `None` is no path at all: both
        # would sidestep every rule below, so neither is a path this permits.
        if not isinstance(path, str | bytes | os.PathLike):
            return False
        resolved: str = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        return resolved.startswith(read_roots)

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event in forbidden_events:
            raise deny(f"{event} is not permitted in the sandbox")

        if event == "open":
            path, mode = args[0], args[1]
            if mode and write_modes & set(str(mode)):
                raise deny(f"writing {path!r} is not permitted in the sandbox")
            if not within_runtime(path):
                raise deny(f"reading {path!r} is not permitted in the sandbox")
            return

        if event in {"os.listdir", "os.scandir"} and not within_runtime(args[0]):
            raise deny(f"listing {args[0]!r} is not permitted in the sandbox")

    sys.addaudithook(hook)


def _namespace(
    modules: dict[str, Any], allowed_imports: frozenset[str], frame: Any
) -> dict[str, Any]:
    """The globals generated code sees.

    The gated ``__import__`` is the *import allowlist*. It stops every ordinary
    route — the ``import`` statement, ``__import__``, ``importlib`` — and it is
    bypassable, which is why it is the weakest of the three layers and why the
    audit hook exists underneath it.

    An audit hook on the ``import`` event would not do: that event is raised by
    ``_find_and_load``, which never runs on a ``sys.modules`` cache hit, so
    ``import socket`` after pandas has loaded it fires nothing at all. Measured.
    """
    import builtins

    real_import = builtins.__import__

    def gated_import(
        name: str,
        globals_: dict[str, Any] | None = None,
        locals_: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        root = name.split(".")[0]
        if root not in allowed_imports:
            DENIALS.append(f"import of {name!r} is not on the sandbox allowlist")
            raise SandboxDenied(f"import of {name!r} is not on the sandbox allowlist")
        return real_import(name, globals_, locals_, fromlist, level)

    safe_builtins = {
        name: getattr(builtins, name) for name in dir(builtins) if not name.startswith("_")
    }
    safe_builtins["__import__"] = gated_import
    safe_builtins["__name__"] = "builtins"

    namespace: dict[str, Any] = {
        "__builtins__": safe_builtins,
        "__name__": "__sandbox__",
        "frame": frame,
        "result": None,
    }
    # The preloaded libraries under their conventional short names, so a model
    # that writes `np.mean(...)` without importing anything still works.
    for short, full in (("np", "numpy"), ("pd", "pandas"), ("sm", "statsmodels.api")):
        if full in modules:
            namespace[short] = modules[full]
    return namespace


# --- output -----------------------------------------------------------------


def _coerce(value: Any) -> Any:
    """Anything the numeric stack produces, as plain JSON.

    numpy scalars are not `float`, numpy arrays are not `list`, and pandas
    indexes are neither — so `json.dumps` fails on the most ordinary result a
    model could write. Converting here rather than in the parent keeps the
    envelope a plain document.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, dict):
        return {str(key): _coerce(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_coerce(item) for item in value]
    for attribute in ("tolist", "to_list"):
        converter = getattr(value, attribute, None)
        if callable(converter):
            return _coerce(converter())
    if hasattr(value, "item"):
        try:
            return _coerce(value.item())
        except (ValueError, TypeError):
            pass
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _describe_failure(exc: BaseException) -> str:
    """Why the generated code failed, without reading a single file.

    `traceback.format_exc` goes through `linecache`, which **opens the source
    of every frame** — including this one, which lives outside the sandbox's
    read roots. So the ordinary path of "generated code raised ZeroDivisionError"
    turned into a filesystem denial and a dead child. Found by a test asserting
    the error names `ZeroDivisionError`.

    Frame summaries are built by hand from the traceback object instead. They
    lose the source line, which is a small price for an error message that
    survives the policy that surrounds it — and for `SyntaxError` the offending
    text rides on the exception itself, so the case that needs it most keeps it.
    """
    head = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    frames: list[str] = []
    tb = exc.__traceback__
    while tb is not None:
        code = tb.tb_frame.f_code
        if code.co_filename != __file__:
            frames.append(f"  line {tb.tb_lineno}, in {code.co_name}")
        tb = tb.tb_next
    return "\n".join([head, *frames[-5:]])


def _emit(envelope: dict[str, Any]) -> None:
    stream = sys.__stdout__
    if stream is None:  # pragma: no cover - stdout is always a pipe here
        return
    stream.write("\n" + "__ECONOMETRICA_SANDBOX__" + json.dumps(envelope) + "\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
