"""Talking to one MCP server, through the allowlist.

**The gate runs before the session is asked.** `require` raises on an unlisted
tool, so a refusal is not "the client chose not to send it" — the server is
never told the tool was wanted. That is the difference the parent plan's
acceptance criterion turns on, and it is why the tests drive a real server that
genuinely offers the forbidden tool and can prove it never ran.

**The allowlist is read per call**, not captured at connect time. A user who
permits a tool should not have to restart anything, and a cached decision is a
decision that can go stale in the permissive direction.

**Discovery is not permission.** `discover` lists everything a server offers,
each marked with whether it is allowed, because that listing is how a user
builds the allowlist in the first place. Showing a tool must never make it
callable.

The MCP SDK's types stop here, the way a provider SDK's stop at
`llm/providers/`: everything above speaks `DiscoveredTool` and `McpCall`.
"""

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

from econometrica.agents.trace import StepRecord
from econometrica.mcp.allowlist import Allowlist, ToolRef

#: How much of a tool's output is kept on the trace step. The output itself
#: goes to the caller; the step is an index into what happened.
_DETAIL_LIMIT = 500


class McpUnavailableError(RuntimeError):
    """The server could not be reached, or the session is gone.

    Distinct from a tool that ran and failed: one is a transport problem and the
    other is a result about the tool, and whoever reads the trace needs to know
    which.
    """


@dataclass(frozen=True)
class DiscoveredTool:
    """One tool a server offers, and whether this project permits it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    allowed: bool


@dataclass(frozen=True)
class McpCall:
    """One completed call, in the shape the trace wants."""

    server: str
    tool: str
    arguments: dict[str, Any]
    output: str
    failed: bool
    latency_ms: float
    #: Of the tool and its arguments, so two runs can be compared for "did this
    #: do the same thing" — the same role `tool_call_hash` plays for a
    #: registry tool.
    call_hash: str = ""

    def to_step_record(self) -> StepRecord:
        """As a step in the run's trace.

        `kind="tool"` and a `mcp:` prefix rather than a new agent or kind: an
        MCP call *is* a tool invocation, and inventing a vocabulary for it would
        mean a migration and a second thing for the trace viewer to understand.
        """
        return StepRecord(
            agent="econometrician",
            kind="tool",
            status="failed" if self.failed else "ok",
            tool=f"mcp:{self.server}:{self.tool}",
            tool_call_hash=self.call_hash,
            latency_ms=self.latency_ms,
            # The arguments are what make the call auditable at all — §9 asks
            # for MCP calls to be attributed, and a tool call nobody can audit
            # is worse than no tool call.
            detail=f"{json.dumps(self.arguments, sort_keys=True, default=str)}"[
                :_DETAIL_LIMIT
            ],
        )


@dataclass
class McpClient:
    """One connected server, gated by one allowlist."""

    session: Any
    server: str
    allowlist: Allowlist = field(default_factory=lambda: Allowlist([]))

    async def discover(self) -> list[DiscoveredTool]:
        """Everything the server offers. Listing is not permitting."""
        try:
            listed = await self.session.list_tools()
        except Exception as exc:
            raise McpUnavailableError(
                f"{self.server}: the MCP server could not be listed ({exc})"
            ) from exc

        return [
            DiscoveredTool(
                name=tool.name,
                description=tool.description or "",
                input_schema=dict(tool.inputSchema or {}),
                allowed=self.allowlist.allows(ToolRef(server=self.server, tool=tool.name)),
            )
            for tool in listed.tools
        ]

    async def call(self, tool: str, arguments: dict[str, Any]) -> McpCall:
        """Invoke a tool, if this project has said it may be invoked."""
        ref = ToolRef(server=self.server, tool=tool)
        # Before anything is sent. A refused tool is never named to the server.
        self.allowlist.require(ref)

        started = time.perf_counter()
        try:
            result = await self.session.call_tool(tool, arguments)
        except Exception as exc:
            raise McpUnavailableError(
                f"{self.server}: calling {tool!r} failed to reach the server ({exc})"
            ) from exc

        return McpCall(
            server=self.server,
            tool=tool,
            arguments=dict(arguments),
            output=_render(result),
            # `isError` means the server ran it and it failed — a result about
            # the tool, not a transport problem.
            failed=bool(getattr(result, "isError", False)),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            call_hash=call_hash(self.server, tool, arguments),
        )


def call_hash(server: str, tool: str, arguments: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"server": server, "tool": tool, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _render(result: Any) -> str:
    """A tool's output as text.

    MCP content is a list of typed blocks; the trace and the agents above want
    one string, and this is the boundary where the SDK's types stop.
    """
    blocks = getattr(result, "content", None) or []
    parts = [str(getattr(block, "text", "")) for block in blocks]
    return "\n".join(part for part in parts if part)
