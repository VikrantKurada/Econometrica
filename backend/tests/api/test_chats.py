from uuid import uuid4

import pytest


async def make_project(client, name="Project", **fields):
    created = (await client.post("/api/projects", json={"name": name})).json()
    if fields:
        created = (await client.patch(f"/api/projects/{created['id']}", json=fields)).json()
    return created


async def make_chat(client, project_id, name="Chat"):
    response = await client.post(f"/api/projects/{project_id}/chats", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_chat_returns_201(client):
    project = await make_project(client, "Momentum")
    response = await client.post(
        f"/api/projects/{project['id']}/chats", json={"name": "Cross-sectional sort"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Cross-sectional sort"
    assert body["project_id"] == project["id"]
    # A new chat inherits everything: neither toggle is an override yet.
    assert body["web_search_enabled"] is None
    assert body["mcp_enabled"] is None


async def test_create_chat_under_missing_project_returns_404(client):
    response = await client.post(f"/api/projects/{uuid4()}/chats", json={"name": "Orphan"})
    assert response.status_code == 404


async def test_create_chat_rejects_blank_name(client):
    project = await make_project(client, "Blank Names")
    response = await client.post(f"/api/projects/{project['id']}/chats", json={"name": "   "})
    assert response.status_code == 422


async def test_list_chats_returns_only_that_projects_chats(client):
    first = await make_project(client, "First")
    second = await make_project(client, "Second")
    await make_chat(client, first["id"], "First A")
    await make_chat(client, first["id"], "First B")
    await make_chat(client, second["id"], "Second A")

    response = await client.get(f"/api/projects/{first['id']}/chats")
    assert response.status_code == 200
    names = {chat["name"] for chat in response.json()}
    assert names == {"First A", "First B"}

    other = await client.get(f"/api/projects/{second['id']}/chats")
    assert {chat["name"] for chat in other.json()} == {"Second A"}


async def test_list_chats_for_missing_project_returns_404(client):
    response = await client.get(f"/api/projects/{uuid4()}/chats")
    assert response.status_code == 404


async def test_rename_chat(client):
    project = await make_project(client, "Renames")
    chat = await make_chat(client, project["id"], "Before")
    response = await client.patch(f"/api/chats/{chat['id']}", json={"name": "After"})
    assert response.status_code == 200
    assert response.json()["name"] == "After"


async def test_chat_toggle_round_trips_override_and_inherit_through_json(client):
    """None, False and True are three different states and must stay distinct.

    Collapsing null into false would make "inherit" indistinguishable from
    "explicitly off" — the chat would keep working by accident while the
    project is on, and break the moment the project is turned off.
    """
    project = await make_project(client, "Three States", web_search_enabled=True)
    chat = await make_chat(client, project["id"])
    assert chat["web_search_enabled"] is None

    overridden_off = await client.patch(
        f"/api/chats/{chat['id']}", json={"web_search_enabled": False}
    )
    assert overridden_off.status_code == 200
    assert overridden_off.json()["web_search_enabled"] is False
    assert (await client.get(f"/api/chats/{chat['id']}")).json()["web_search_enabled"] is False

    overridden_on = await client.patch(
        f"/api/chats/{chat['id']}", json={"web_search_enabled": True}
    )
    assert overridden_on.json()["web_search_enabled"] is True

    reverted = await client.patch(f"/api/chats/{chat['id']}", json={"web_search_enabled": None})
    assert reverted.status_code == 200
    assert reverted.json()["web_search_enabled"] is None
    assert (await client.get(f"/api/chats/{chat['id']}")).json()["web_search_enabled"] is None


async def test_patch_chat_name_leaves_overrides_untouched(client):
    project = await make_project(client, "Partial Chat Update")
    chat = await make_chat(client, project["id"])
    await client.patch(f"/api/chats/{chat['id']}", json={"web_search_enabled": False})

    renamed = await client.patch(f"/api/chats/{chat['id']}", json={"name": "Renamed"})

    assert renamed.status_code == 200
    body = renamed.json()
    assert body["name"] == "Renamed"
    assert body["web_search_enabled"] is False


@pytest.mark.parametrize(
    ("project_fields", "chat_override", "expected"),
    [
        # Project on, chat inheriting.
        ({"web_search_enabled": True}, {}, True),
        # Project on, chat explicitly off.
        ({"web_search_enabled": True}, {"web_search_enabled": False}, False),
        # Project off, chat explicitly on.
        ({}, {"web_search_enabled": True}, True),
        # Project off, chat inheriting.
        ({}, {}, False),
    ],
)
async def test_capabilities_resolve_project_and_chat(
    client, project_fields, chat_override, expected
):
    project = await make_project(client, "Capabilities", **project_fields)
    chat = await make_chat(client, project["id"])
    if chat_override:
        await client.patch(f"/api/chats/{chat['id']}", json=chat_override)

    response = await client.get(f"/api/chats/{chat['id']}/capabilities")

    assert response.status_code == 200
    assert response.json()["web_search"] is expected


async def test_capabilities_include_project_only_settings(client):
    """The sandbox and the validation tier are project scoped, never overridden."""
    project = await make_project(
        client,
        "Sandbox",
        code_sandbox_enabled=True,
        mcp_enabled=True,
        validation_tier="consensus",
    )
    chat = await make_chat(client, project["id"])

    body = (await client.get(f"/api/chats/{chat['id']}/capabilities")).json()

    assert body == {
        "web_search": False,
        "mcp": True,
        "code_sandbox": True,
        "validation_tier": "consensus",
    }


async def test_capabilities_for_missing_chat_returns_404(client):
    assert (await client.get(f"/api/chats/{uuid4()}/capabilities")).status_code == 404


async def test_delete_chat_returns_204(client):
    project = await make_project(client, "Deletions")
    chat = await make_chat(client, project["id"], "Doomed")
    response = await client.delete(f"/api/chats/{chat['id']}")
    assert response.status_code == 204
    assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 404


async def test_deleting_a_project_deletes_its_chats(client):
    project = await make_project(client, "Cascade")
    survivor_project = await make_project(client, "Survivor")
    doomed = await make_chat(client, project["id"], "Doomed chat")
    survivor = await make_chat(client, survivor_project["id"], "Surviving chat")

    assert (await client.delete(f"/api/projects/{project['id']}")).status_code == 204

    assert (await client.get(f"/api/chats/{doomed['id']}")).status_code == 404
    assert (await client.get(f"/api/chats/{survivor['id']}")).status_code == 200


async def test_unknown_chat_id_returns_404_everywhere(client):
    missing = uuid4()
    assert (await client.get(f"/api/chats/{missing}")).status_code == 404
    assert (await client.patch(f"/api/chats/{missing}", json={"name": "Ghost"})).status_code == 404
    assert (await client.delete(f"/api/chats/{missing}")).status_code == 404
