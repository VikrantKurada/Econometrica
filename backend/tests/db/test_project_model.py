import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from econometrica.db.models import Chat, Project


@pytest.mark.parametrize("tier", ["single", "critic", "consensus"])
async def test_database_accepts_every_legal_validation_tier(session, tier):
    session.add(Project(name=f"Tier {tier}", validation_tier=tier))
    await session.flush()


async def test_database_rejects_an_invalid_validation_tier(session):
    """The API validates the tier, but a raw insert must not get past the schema.

    resolve_capabilities hands this value to the orchestrator, so an unknown
    tier written out of band would surface as a runtime failure far from its
    cause. Same argument that put the server_defaults in the schema.
    """
    with pytest.raises(IntegrityError):
        await session.execute(
            text("INSERT INTO projects (name, validation_tier) VALUES ('Raw', 'banana')")
        )


async def test_project_persists_with_defaults(session):
    project = Project(name="Equity Factor Study")
    session.add(project)
    await session.flush()
    assert project.id is not None
    assert project.web_search_enabled is False
    assert project.mcp_enabled is False
    assert project.validation_tier == "critic"


async def test_project_can_be_renamed(session):
    project = Project(name="Old Name")
    session.add(project)
    await session.flush()
    project.name = "New Name"
    await session.flush()
    fetched = await session.scalar(select(Project).where(Project.id == project.id))
    assert fetched.name == "New Name"


async def test_chat_belongs_to_project_and_cascades_on_delete(session):
    project = Project(name="Crypto Efficiency")
    chat = Chat(name="BTC variance ratio", project=project)
    session.add(project)
    await session.flush()
    assert chat.project_id == project.id
    await session.delete(project)
    await session.flush()
    remaining = await session.scalars(select(Chat))
    assert remaining.all() == []


async def test_project_name_cannot_be_empty(session):
    session.add(Project(name=""))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_defaults_apply_to_inserts_that_bypass_the_orm(session):
    """Seed scripts, data migrations and psql insert without SQLAlchemy's defaults.

    Every NOT NULL column that the ORM fills in Python must therefore also carry
    a server-side default, or the schema is only usable from the ORM.
    """
    await session.execute(text("INSERT INTO projects (name) VALUES ('Raw Insert')"))
    project = (
        await session.execute(
            text(
                "SELECT id, web_search_enabled, mcp_enabled, code_sandbox_enabled, "
                "validation_tier, model_assignments, created_at, updated_at "
                "FROM projects WHERE name = 'Raw Insert'"
            )
        )
    ).one()

    assert project.id is not None
    assert project.web_search_enabled is False
    assert project.mcp_enabled is False
    assert project.code_sandbox_enabled is False
    assert project.validation_tier == "critic"
    assert project.model_assignments == {}
    assert project.created_at is not None
    assert project.updated_at is not None

    await session.execute(
        text("INSERT INTO chats (name, project_id) VALUES ('Raw Chat', :project_id)"),
        {"project_id": project.id},
    )
    chat = (
        await session.execute(
            text(
                "SELECT id, web_search_enabled, mcp_enabled FROM chats WHERE name = 'Raw Chat'"
            )
        )
    ).one()

    assert chat.id is not None
    # Nullable by design: NULL is the "inherit from project" state, so the chat
    # toggles deliberately have no server default.
    assert chat.web_search_enabled is None
    assert chat.mcp_enabled is None


async def test_chat_capability_toggles_default_to_none_meaning_inherit(session):
    """None means "inherit from project" and must stay distinct from False ("off")."""
    project = Project(name="Three State Toggles")
    inheriting = Chat(name="Inherits from project", project=project)
    explicitly_off = Chat(name="Explicitly off", project=project, web_search_enabled=False)
    session.add(project)
    await session.flush()
    inheriting_id, explicitly_off_id = inheriting.id, explicitly_off.id

    # Drop every in-memory value so the assertions below read the stored rows,
    # proving the column really is NULL rather than merely unset on the instance.
    session.expire_all()

    fetched_inheriting = await session.scalar(select(Chat).where(Chat.id == inheriting_id))
    fetched_off = await session.scalar(select(Chat).where(Chat.id == explicitly_off_id))

    assert fetched_inheriting.web_search_enabled is None
    assert fetched_inheriting.mcp_enabled is None
    assert fetched_off.web_search_enabled is False
