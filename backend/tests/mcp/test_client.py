"""The MCP client, against a real MCP server.

The SDK ships an in-memory transport, so these connect a genuine `FastMCP`
server to a genuine `ClientSession` — no mock anywhere. That matters most for
the parent plan's named criterion: **an unlisted tool cannot be invoked**. A
mock proves the client declined to ask; a real server that genuinely offers
`delete` and never runs it proves the gate.
"""

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from econometrica.mcp.allowlist import Allowlist, ToolNotAllowedError
from econometrica.mcp.client import McpCall, McpClient, McpUnavailableError

#: Recorded by the server's own tools, so a test can tell "refused" from
#: "ran and returned nothing" — which is the distinction the whole gate is for.
EXECUTED: list[str] = []


def build_server() -> FastMCP:
    server = FastMCP("files")

    @server.tool()
    def read(path: str) -> str:
        """Read a file."""
        EXECUTED.append(f"read:{path}")
        return f"contents of {path}"

    @server.tool()
    def delete(path: str) -> str:
        """Delete a file. Deliberately offered, and never allowed."""
        EXECUTED.append(f"delete:{path}")
        return f"deleted {path}"

    return server


@pytest.fixture(autouse=True)
def clean():
    EXECUTED.clear()
    yield
    EXECUTED.clear()


async def client_for(allowlist: list[str]):
    """A connected client over the in-memory transport."""
    return create_connected_server_and_client_session(build_server()), Allowlist(allowlist)


# --- the named criterion ------------------------------------------------------


async def test_an_unlisted_tool_cannot_be_invoked():
    """The parent plan's acceptance criterion, against a server that really
    offers the tool. The proof is `EXECUTED`: the server never ran it."""
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)

        with pytest.raises(ToolNotAllowedError, match="files:delete"):
            await client.call("delete", {"path": "/etc/passwd"})

    assert EXECUTED == [], "the server must never have been asked"


async def test_an_allowlisted_tool_runs():
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        call = await client.call("read", {"path": "/tmp/x"})

    assert EXECUTED == ["read:/tmp/x"]
    assert "contents of /tmp/x" in call.output


async def test_an_empty_allowlist_runs_nothing():
    session_cm, allowlist = await client_for([])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)

        with pytest.raises(ToolNotAllowedError):
            await client.call("read", {"path": "/tmp/x"})

    assert EXECUTED == []


# --- discovery is not permission ----------------------------------------------


async def test_discovery_lists_every_tool_the_server_offers():
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        found = await client.discover()

    assert sorted(tool.name for tool in found) == ["delete", "read"]


async def test_discovery_marks_which_tools_are_allowed_without_permitting_them():
    """Listing is how a user builds an allowlist, so it must show what it does
    not permit — and showing it must not permit it."""
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        found = {tool.name: tool for tool in await client.discover()}

        assert found["read"].allowed is True
        assert found["delete"].allowed is False

        with pytest.raises(ToolNotAllowedError):
            await client.call("delete", {"path": "/x"})

    assert EXECUTED == []


async def test_discovery_carries_the_description_and_schema():
    """What a user needs to decide whether to allow something."""
    session_cm, allowlist = await client_for([])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        found = {tool.name: tool for tool in await client.discover()}

    assert "Read a file" in found["read"].description
    assert "path" in found["read"].input_schema["properties"]


# --- the allowlist is consulted per call --------------------------------------


async def test_an_allowlist_change_takes_effect_without_reconnecting():
    """A user who permits a tool should not have to restart anything. The gate
    is read per call, so there is no cached decision to go stale."""
    session_cm, _ = await client_for([])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=Allowlist([]))

        with pytest.raises(ToolNotAllowedError):
            await client.call("read", {"path": "/tmp/x"})

        client.allowlist = Allowlist(["files:read"])
        await client.call("read", {"path": "/tmp/x"})

    assert EXECUTED == ["read:/tmp/x"]


# --- failure ------------------------------------------------------------------


async def test_a_server_that_disappears_fails_the_call_not_the_process():
    """A step that could not run is a finding; an exception escaping into the
    orchestrator would take the whole run with it."""
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)

    # The session is closed now — the server is gone as far as this client knows.
    with pytest.raises(McpUnavailableError, match="files"):
        await client.call("read", {"path": "/tmp/x"})


async def test_a_tool_that_errors_is_reported_rather_than_raised():
    """The server ran it and it failed. That is a result about the tool, not a
    transport problem, and the difference matters to whoever reads the trace."""
    server = FastMCP("files")

    @server.tool()
    def explode() -> str:
        raise RuntimeError("no such file")

    async with create_connected_server_and_client_session(server) as session:
        client = McpClient(
            session=session, server="files", allowlist=Allowlist(["files:explode"])
        )
        call = await client.call("explode", {})

    assert call.failed is True
    assert "no such file" in call.output


async def test_discovery_on_a_closed_session_is_reported(clean):
    session_cm, allowlist = await client_for([])
    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)

    with pytest.raises(McpUnavailableError):
        await client.discover()


# --- what the trace gets ------------------------------------------------------


async def test_a_call_records_what_it_asked_and_what_came_back():
    """§9 says MCP calls are attributed in the trace. A tool call nobody can
    audit is worse than no tool call."""
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        call = await client.call("read", {"path": "/tmp/x"})

    assert isinstance(call, McpCall)
    assert call.server == "files"
    assert call.tool == "read"
    assert call.arguments == {"path": "/tmp/x"}
    assert call.failed is False
    assert call.latency_ms >= 0


async def test_a_call_becomes_a_trace_step():
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        call = await client.call("read", {"path": "/tmp/x"})

    step = call.to_step_record()

    assert step.kind == "tool"
    assert step.tool == "mcp:files:read"
    assert step.status == "ok"
    # The arguments are what makes the call auditable at all.
    assert "/tmp/x" in step.detail


async def test_a_failed_call_becomes_a_failed_step():
    server = FastMCP("files")

    @server.tool()
    def explode() -> str:
        raise RuntimeError("no such file")

    async with create_connected_server_and_client_session(server) as session:
        client = McpClient(
            session=session, server="files", allowlist=Allowlist(["files:explode"])
        )
        call = await client.call("explode", {})

    assert call.to_step_record().status == "failed"


async def test_the_hash_distinguishes_different_arguments():
    """Two calls to the same tool with different arguments are different work,
    and a trace that could not tell them apart could not be compared."""
    session_cm, allowlist = await client_for(["files:read"])

    async with session_cm as session:
        client = McpClient(session=session, server="files", allowlist=allowlist)
        first = await client.call("read", {"path": "/a"})
        second = await client.call("read", {"path": "/b"})
        again = await client.call("read", {"path": "/a"})

    assert first.call_hash != second.call_hash
    assert first.call_hash == again.call_hash
