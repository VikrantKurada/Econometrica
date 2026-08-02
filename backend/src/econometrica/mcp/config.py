"""How a project names an MCP server, typed.

`project.mcp_servers` is a JSONB list; this is the shape each entry must take
before anything tries to connect from it. Kept free of the MCP SDK — the SDK's
transport types belong to `connect.py`, this is just the user's declared intent.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator


class McpServerConfig(BaseModel):
    """One MCP server a project may connect to."""

    name: str = Field(min_length=1)
    transport: Literal["stdio", "http"]

    #: stdio: a local command, spawned with host privileges — see the design
    #: note's trust model. Not sandboxed; configuring one is trusting it.
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    #: http: an already-running server reached by URL.
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def transport_fields_must_be_present(self) -> Self:
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"stdio server {self.name!r} needs a command to spawn")
        if self.transport == "http" and not self.url:
            raise ValueError(f"http server {self.name!r} needs a url to connect to")
        return self

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "McpServerConfig":
        """Parse one entry from `project.mcp_servers`."""
        return cls.model_validate(data)
