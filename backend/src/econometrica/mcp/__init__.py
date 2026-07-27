"""The application as an MCP client.

§9 of the design: "The application acts as an MCP client; discovered tools pass
through an explicit allowlist before any agent may call them." Both halves
matter, and they are separate modules on purpose.

`allowlist.py` is the gate and has no network in it at all, so the security
property is testable as arithmetic. `client.py` connects, discovers and calls —
and cannot call anything the gate has not passed, because `require` runs before
the session is even asked.

MCP is **off by default** at the project level and its allowlist is **empty by
default**, so a project that turns the capability on has still consented to
nothing until it names tools one at a time.
"""

from econometrica.mcp.allowlist import Allowlist, ToolNotAllowedError, ToolRef

__all__ = ["Allowlist", "ToolNotAllowedError", "ToolRef"]
