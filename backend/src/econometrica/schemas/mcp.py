"""What the MCP discovery endpoint puts on the wire."""

from typing import Any

from pydantic import BaseModel


class McpToolRead(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    allowed: bool


class McpServerToolsRead(BaseModel):
    server: str
    tools: list[McpToolRead]
    #: Set when the server could not be reached; `tools` is then empty.
    error: str = ""
