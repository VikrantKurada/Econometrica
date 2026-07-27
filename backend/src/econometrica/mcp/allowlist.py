"""Which discovered MCP tools an agent may actually call.

The security surface of the whole feature, kept free of network code so that it
is testable as arithmetic. Three properties carry it:

**Default deny.** An empty allowlist allows nothing. Turning the capability on
is not consent to whatever a server happens to offer, and a project that has
listed nothing has agreed to nothing.

**Explicit, not patterned.** §9 asks for an *explicit* allowlist, so there are
no wildcards: `files:*` is a literal tool name that matches a tool actually
called `*`. A pattern would silently re-admit whatever a server added next,
which is the failure this exists to prevent.

**Exact matching.** A server chooses its own tool names. Case-folding or
trimming would make the gate depend on a normalisation the server never agreed
to, and `files:read` and `shell:read` are different tools — matching on the tool
name alone would let a second server impersonate a trusted one.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: Separates the server from the tool. Only the first one splits, so a server
#: that namespaces its own tools stays addressable.
SEPARATOR = ":"


class ToolNotAllowedError(PermissionError):
    """A tool was asked for that no allowlist entry names."""


@dataclass(frozen=True)
class ToolRef:
    """One tool on one server."""

    server: str
    tool: str

    def __str__(self) -> str:
        return f"{self.server}{SEPARATOR}{self.tool}"

    @classmethod
    def parse(cls, entry: str) -> "ToolRef | None":
        """A `server:tool` entry, or None where it names no tool.

        None rather than a raise: a malformed entry in a user's configuration
        should cost that entry, not the whole allowlist — and certainly not fail
        open.
        """
        server, found, tool = entry.partition(SEPARATOR)
        if not found or not server or not tool:
            return None
        return cls(server=server, tool=tool)


class Allowlist:
    """The set of tools an agent may invoke, and nothing else."""

    def __init__(self, entries: Iterable[str]) -> None:
        self.entries = tuple(entries)
        # Malformed entries are dropped here rather than at the gate, so
        # `allows` is a set membership test and cannot grow a special case.
        self._refs = frozenset(
            ref for ref in (ToolRef.parse(entry) for entry in self.entries) if ref
        )

    def allows(self, ref: ToolRef) -> bool:
        return ref in self._refs

    def require(self, ref: ToolRef) -> None:
        """Raise unless this tool is listed."""
        if self.allows(ref):
            return
        if not self._refs:
            raise ToolNotAllowedError(
                f"{ref} was not called: no MCP tools are allowed for this project."
                " Add it to the project's allowlist to permit it."
            )
        raise ToolNotAllowedError(
            f"{ref} was not called: it is not in this project's MCP allowlist."
            f" Allowed: {', '.join(sorted(str(entry) for entry in self._refs))}"
        )

    @classmethod
    def for_project(cls, project: Any) -> "Allowlist":
        """The allowlist a project has configured, empty when it has none."""
        return cls(getattr(project, "mcp_allowlist", None) or [])

    def __len__(self) -> int:
        return len(self._refs)
