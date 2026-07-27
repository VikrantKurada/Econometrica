"""The code escape hatch: generated code, run where it can do least.

§2 of the design chose registry-first *with* a gated escape hatch, and this is
the gate. The registry in `econ/` holds the thirty-seven typed, versioned,
tested functions the project's one invariant is about; when a question fits
none of them, a model writes code and this package runs it in a subprocess with
no network, no filesystem to speak of, an import allowlist and OS-enforced
caps.

**A result from here is not a result from there, and must never look like
one.** It has no tested function behind it, so `agents/quant_coder.py` marks it
— `tool="sandbox:<slug>"`, `tool_version="unvalidated"` — and the run carries
an `unvalidated_method` flag the canvas shows the way it shows
`synthetic_data`. Marking is the load-bearing part of this feature; the
restrictions merely keep the process from taking the machine with it.

Off by default, project-scoped only, and the Validator must sign off.
"""
