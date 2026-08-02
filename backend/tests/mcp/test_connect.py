"""Connecting to real MCP servers, and degrading when one will not."""

import sys
from pathlib import Path

from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.config import McpServerConfig
from econometrica.mcp.connect import McpConnector, connect_server

_ECHO = Path(__file__).parent / "fixtures" / "echo_server.py"


def _stdio(name="echo") -> McpServerConfig:
    return McpServerConfig(
        name=name, transport="stdio", command=sys.executable, args=[str(_ECHO)]
    )


async def test_connect_stdio_round_trips_against_a_real_server():
    """Spawns the fixture over stdio, discovers and calls a tool. Offline, but a
    real subprocess and a real session — the transport code cannot be proven by
    the in-memory harness, which hands back a session already connected."""
    async with connect_server(_stdio(), Allowlist(["echo:echo"])) as client:
        found = {t.name for t in await client.discover()}
        assert "echo" in found

        call = await client.call("echo", {"text": "hi"})
    assert "echo: hi" in call.output


async def test_the_connector_yields_a_gated_client_per_reachable_server():
    connector = McpConnector([_stdio("echo")], Allowlist(["echo:echo"]))

    async with connector.open() as clients:
        assert [c.server for c in clients] == ["echo"]
        call = await clients[0].call("echo", {"text": "x"})
    assert "echo: x" in call.output


async def test_an_unreachable_server_is_skipped_not_raised():
    """A bad command must not take the whole connect down — the other servers,
    and the run, survive it."""
    good = _stdio("echo")
    bad = McpServerConfig(name="broken", transport="stdio", command="does-not-exist-xyz")
    connector = McpConnector([bad, good], Allowlist(["echo:echo"]))

    async with connector.open() as clients:
        # The broken server is dropped; the good one remains.
        assert [c.server for c in clients] == ["echo"]
