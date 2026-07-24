from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from econometrica.api.deps import SessionDep, get_chat_or_404, get_project_or_404
from econometrica.db.models import Chat
from econometrica.schemas.chat import CapabilitiesRead, ChatCreate, ChatRead, ChatUpdate
from econometrica.services.capabilities import resolve_capabilities

# Chats are addressed two ways: nested under their project for creation and
# listing, and directly by id once the client holds one.
router = APIRouter(prefix="/api", tags=["chats"])


@router.post("/projects/{project_id}/chats", status_code=status.HTTP_201_CREATED)
async def create_chat(project_id: UUID, payload: ChatCreate, session: SessionDep) -> ChatRead:
    await get_project_or_404(session, project_id)
    chat = Chat(project_id=project_id, **payload.model_dump())
    session.add(chat)
    await session.commit()
    return ChatRead.model_validate(chat)


@router.get("/projects/{project_id}/chats")
async def list_chats(project_id: UUID, session: SessionDep) -> list[ChatRead]:
    # Resolve the project first so an unknown id is a 404 rather than an empty
    # list, which the UI could not tell apart from a project with no chats.
    await get_project_or_404(session, project_id)
    chats = await session.scalars(
        select(Chat).where(Chat.project_id == project_id).order_by(Chat.created_at, Chat.name)
    )
    return [ChatRead.model_validate(chat) for chat in chats]


@router.get("/chats/{chat_id}")
async def get_chat(chat_id: UUID, session: SessionDep) -> ChatRead:
    return ChatRead.model_validate(await get_chat_or_404(session, chat_id))


@router.patch("/chats/{chat_id}")
async def update_chat(chat_id: UUID, payload: ChatUpdate, session: SessionDep) -> ChatRead:
    chat = await get_chat_or_404(session, chat_id)
    # exclude_unset keeps an omitted toggle untouched, while an explicitly sent
    # null clears the override and restores inheritance.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(chat, field, value)
    await session.commit()
    return ChatRead.model_validate(chat)


@router.get("/chats/{chat_id}/capabilities")
async def get_chat_capabilities(chat_id: UUID, session: SessionDep) -> CapabilitiesRead:
    chat = await get_chat_or_404(session, chat_id, options=[selectinload(Chat.project)])
    return CapabilitiesRead.model_validate(resolve_capabilities(chat.project, chat))


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: UUID, session: SessionDep) -> None:
    chat = await get_chat_or_404(session, chat_id)
    await session.delete(chat)
    await session.commit()
