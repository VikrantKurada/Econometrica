from uuid import uuid4


async def test_create_project_returns_201_with_id(client):
    response = await client.post("/api/projects", json={"name": "FX Carry"})
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "FX Carry"
    assert body["validation_tier"] == "critic"
    assert body["web_search_enabled"] is False


async def test_list_projects_returns_created_projects(client):
    await client.post("/api/projects", json={"name": "Alpha"})
    await client.post("/api/projects", json={"name": "Beta"})
    response = await client.get("/api/projects")
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert {"Alpha", "Beta"} <= set(names)


async def test_rename_project(client):
    created = (await client.post("/api/projects", json={"name": "Before"})).json()
    response = await client.patch(f"/api/projects/{created['id']}", json={"name": "After"})
    assert response.status_code == 200
    assert response.json()["name"] == "After"


async def test_toggle_web_search_on_project(client):
    created = (await client.post("/api/projects", json={"name": "P"})).json()
    response = await client.patch(
        f"/api/projects/{created['id']}", json={"web_search_enabled": True}
    )
    assert response.json()["web_search_enabled"] is True


async def test_delete_project_returns_204(client):
    created = (await client.post("/api/projects", json={"name": "Doomed"})).json()
    response = await client.delete(f"/api/projects/{created['id']}")
    assert response.status_code == 204
    follow_up = await client.get(f"/api/projects/{created['id']}")
    assert follow_up.status_code == 404


async def test_create_project_rejects_blank_name(client):
    response = await client.post("/api/projects", json={"name": "   "})
    assert response.status_code == 422


async def test_patch_with_only_name_leaves_other_fields_untouched(client):
    """A PATCH must apply the fields it was sent and nothing else.

    Serialising the whole model back would silently reset every omitted field
    to its schema default — here, turning web search back off.
    """
    created = (await client.post("/api/projects", json={"name": "Partial"})).json()
    enabled = (
        await client.patch(
            f"/api/projects/{created['id']}",
            json={
                "web_search_enabled": True,
                "mcp_enabled": True,
                "validation_tier": "consensus",
                "description": "keep me",
            },
        )
    ).json()
    assert enabled["web_search_enabled"] is True

    renamed = await client.patch(f"/api/projects/{created['id']}", json={"name": "Renamed"})

    assert renamed.status_code == 200
    body = renamed.json()
    assert body["name"] == "Renamed"
    assert body["web_search_enabled"] is True
    assert body["mcp_enabled"] is True
    assert body["validation_tier"] == "consensus"
    assert body["description"] == "keep me"

    # And the same is true of what was actually stored, not just what was echoed.
    refetched = (await client.get(f"/api/projects/{created['id']}")).json()
    assert refetched["web_search_enabled"] is True
    assert refetched["validation_tier"] == "consensus"


async def test_patch_can_clear_description_explicitly(client):
    """Sending an explicit null is different from omitting the field."""
    created = (
        await client.post("/api/projects", json={"name": "Described", "description": "text"})
    ).json()
    response = await client.patch(f"/api/projects/{created['id']}", json={"description": None})
    assert response.status_code == 200
    assert response.json()["description"] is None


async def test_patch_rejects_explicit_null_for_non_nullable_fields(client):
    """An omitted field means "leave it alone"; an explicit null does not.

    The toggles are NOT NULL columns, so a literal null is a client error and
    must be refused at the edge rather than raised as an IntegrityError.
    """
    created = (await client.post("/api/projects", json={"name": "Nullable"})).json()
    for field in ("web_search_enabled", "mcp_enabled", "code_sandbox_enabled", "validation_tier"):
        response = await client.patch(f"/api/projects/{created['id']}", json={field: None})
        assert response.status_code == 422, field


async def test_update_project_rejects_unknown_validation_tier(client):
    created = (await client.post("/api/projects", json={"name": "Tiered"})).json()
    response = await client.patch(
        f"/api/projects/{created['id']}", json={"validation_tier": "supreme"}
    )
    assert response.status_code == 422


async def test_rename_project_rejects_blank_name(client):
    """The blank-name rule must hold on update as well as on create.

    Otherwise the request reaches the database and trips the check constraint,
    turning a user error into a 500.
    """
    created = (await client.post("/api/projects", json={"name": "Named"})).json()
    response = await client.patch(f"/api/projects/{created['id']}", json={"name": "  "})
    assert response.status_code == 422


async def test_unknown_project_id_returns_404_everywhere(client):
    missing = uuid4()
    assert (await client.get(f"/api/projects/{missing}")).status_code == 404
    assert (
        await client.patch(f"/api/projects/{missing}", json={"name": "Ghost"})
    ).status_code == 404
    assert (await client.delete(f"/api/projects/{missing}")).status_code == 404
