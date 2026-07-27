"""Capabilities an agent may use that are not econometric tools.

The registry in `econ/` holds the thirty-seven typed statistical functions and
is the thing this project's one invariant is about. What lives here is
different: ways of gathering *context* — web search now, and whatever follows.

The distinction is load-bearing rather than organisational. Nothing in this
package may become a source of numbers: the grounding gate admits only values a
tool in `econ/` computed, and text gathered here is exactly as ungrounded as
text a model invented. Each module says so, and each has a test proving the gate
still blocks a figure quoted verbatim from what it returned.
"""
