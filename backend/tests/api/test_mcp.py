"""Discovering a project's MCP tools, to build the allowlist. Listing is not
permitting: a tool shows as not-allowed and stays uncallable."""

import sys
from pathlib import Path

_ECHO = str(Path(__file__).resolve().parents[1] / "mcp" / "fixtures" / "echo_server.py")


async def _project_with_echo(client, allowlist):
    project = (await client.post("/api/projects", json={"name": "MCP"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "mcp_enabled": True,
            "mcp_servers": [
                {"name": "echo", "transport": "stdio", "command": sys.executable, "args": [_ECHO]}
            ],
            "mcp_allowlist": allowlist,
        },
    )
    return project["id"]


async def test_discovery_lists_tools_with_their_allowed_flag(client):
    project_id = await _project_with_echo(client, ["echo:echo"])

    response = await client.get(f"/api/projects/{project_id}/mcp/tools")

    assert response.status_code == 200
    servers = {s["server"]: s for s in response.json()}
    tools = {t["name"]: t for t in servers["echo"]["tools"]}
    assert tools["echo"]["allowed"] is True


async def test_a_tool_not_in_the_allowlist_shows_as_not_allowed(client):
    project_id = await _project_with_echo(client, [])  # allow nothing

    response = await client.get(f"/api/projects/{project_id}/mcp/tools")

    tools = {t["name"]: t for s in response.json() for t in s["tools"]}
    assert tools["echo"]["allowed"] is False


async def test_an_invalid_server_config_is_rejected_on_update(client):
    project = (await client.post("/api/projects", json={"name": "Bad"})).json()

    response = await client.patch(
        f"/api/projects/{project['id']}",
        json={"mcp_servers": [{"name": "x", "transport": "stdio"}]},  # no command
    )

    assert response.status_code == 422
