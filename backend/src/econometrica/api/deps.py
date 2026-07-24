"""Dependencies and lookup helpers shared by every router."""

from collections.abc import Sequence
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption

from econometrica.db.models import Chat, Project
from econometrica.db.session import get_session

# Declared as an ``Annotated`` alias rather than a ``Depends()`` default so that
# route signatures stay free of mutable-looking defaults and the tests can
# override ``get_session`` in one place.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _not_found(entity: str, entity_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} {entity_id} not found"
    )


async def get_project_or_404(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    return project


async def get_chat_or_404(
    session: AsyncSession, chat_id: UUID, options: Sequence[ORMOption] = ()
) -> Chat:
    """Load a chat, optionally with eager loaders.

    Relationships must be loaded up front: touching an unloaded one later in an
    async request raises MissingGreenlet rather than emitting a lazy SELECT.
    """
    loader_options: dict[str, Any] = {"options": list(options)} if options else {}
    chat = await session.get(Chat, chat_id, **loader_options)
    if chat is None:
        raise _not_found("Chat", chat_id)
    return chat
