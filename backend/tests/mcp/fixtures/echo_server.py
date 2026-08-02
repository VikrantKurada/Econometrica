"""A real MCP server over stdio, for the connection round-trip test."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo: {text}"


if __name__ == "__main__":
    server.run()  # stdio by default
