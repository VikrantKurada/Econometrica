import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from econometrica.db.models import Chat, Project


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
