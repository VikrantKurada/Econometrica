"""Chat history and the streaming reply endpoint.

The contract this router keeps is that **the transcript survives whatever the
provider does**. A reply that streams cleanly, one that dies halfway, one from
a provider that was never configured — each leaves the conversation readable
afterwards. A chat that silently loses turns is worse than one that shows a
failed turn, so a failed generation is persisted with its error rather than
dropped.

Ordering of writes matters and is deliberate:

1. Validate the provider *before* writing anything. A turn that cannot
   possibly be answered should not enter the transcript at all.
2. Commit the user's message before generation starts, so the question they
   typed survives a crash, a disconnect or a provider outage.
3. Commit the assistant's message when the turn ends, successfully or not.
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from econometrica.api.deps import ProviderRegistryDep, SessionDep, get_chat_or_404
from econometrica.db.models import Message
from econometrica.llm.errors import ProviderError
from econometrica.llm.types import Message as LLMMessage
from econometrica.llm.types import Role
from econometrica.schemas.message import MessageRead, MessageSend

router = APIRouter(prefix="/api/chats", tags=["messages"])


@router.get("/{chat_id}/messages", response_model=list[MessageRead])
async def list_messages(chat_id: UUID, session: SessionDep) -> list[Message]:
    await get_chat_or_404(session, chat_id)
    rows = await session.scalars(
        select(Message).where(Message.chat_id == chat_id).order_by(Message.seq)
    )
    return list(rows)


@router.post("/{chat_id}/messages")
async def send_message(
    chat_id: UUID,
    payload: MessageSend,
    session: SessionDep,
    registry: ProviderRegistryDep,
) -> EventSourceResponse:
    """Persist a user turn and stream the reply back as server-sent events."""
    await get_chat_or_404(session, chat_id)

    try:
        spec = registry.spec(payload.provider)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown provider {payload.provider!r}",
        ) from exc

    # Refuse before writing: a turn nothing can answer should not be recorded.
    if not registry.is_configured(payload.provider):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{spec.label} has no api key configured",
        )

    history = list(
        await session.scalars(
            select(Message).where(Message.chat_id == chat_id).order_by(Message.seq)
        )
    )

    user_message = Message(chat_id=chat_id, role="user", content=payload.content)
    session.add(user_message)
    # Commit before generating: the user's question must outlive a provider
    # outage or a dropped connection.
    await session.commit()
    await session.refresh(user_message)

    conversation = [*_to_llm_messages(history), LLMMessage.user(payload.content)]
    provider = registry.build(payload.provider)

    async def events() -> AsyncIterator[dict[str, str]]:
        yield _event("start", {"user_message": _dump(user_message)})

        assistant = Message(
            chat_id=chat_id,
            role="assistant",
            content="",
            provider=payload.provider,
            model=payload.model,
        )
        parts: list[str] = []
        started = time.perf_counter()

        try:
            async for chunk in provider.stream(
                conversation, model=payload.model, temperature=0.0
            ):
                if chunk.delta:
                    parts.append(chunk.delta)
                    yield _event("delta", {"text": chunk.delta})
                if chunk.done:
                    assistant.stop_reason = chunk.stop_reason
                    if chunk.usage is not None:
                        assistant.input_tokens = chunk.usage.input_tokens
                        assistant.output_tokens = chunk.usage.output_tokens
                        assistant.cache_read_tokens = chunk.usage.cache_read_tokens
                        assistant.cache_write_tokens = chunk.usage.cache_write_tokens
        except ProviderError as exc:
            assistant.error = str(exc)
        except Exception as exc:
            # An adapter is only supposed to raise ProviderError, but an
            # unexpected failure must still leave a readable transcript rather
            # than a half-written turn.
            assistant.error = f"{type(exc).__name__}: {exc}"

        assistant.content = "".join(parts)
        assistant.latency_ms = (time.perf_counter() - started) * 1000.0
        session.add(assistant)
        await session.commit()
        await session.refresh(assistant)

        if assistant.error:
            yield _event("error", {"detail": assistant.error, "message": _dump(assistant)})
        else:
            yield _event("done", {"message": _dump(assistant)})

    return EventSourceResponse(events())


# --- internals --------------------------------------------------------------


def _to_llm_messages(history: list[Message]) -> list[LLMMessage]:
    """Replay stored turns as provider-neutral messages.

    Failed turns are skipped: their content is empty and resending them would
    teach the model that blank assistant replies are acceptable.
    """
    replayed: list[LLMMessage] = []
    for row in history:
        if row.error or not row.content:
            continue
        role = Role(row.role)
        if role is Role.ASSISTANT:
            replayed.append(LLMMessage.assistant(row.content))
        elif role is Role.SYSTEM:
            replayed.append(LLMMessage.system(row.content))
        else:
            replayed.append(LLMMessage.user(row.content))
    return replayed


def _dump(message: Message) -> dict[str, Any]:
    """Round-trip through the read schema so the wire shape matches the REST one.

    Going via JSON rather than ``model_dump()`` also normalises UUIDs and
    datetimes, which are not directly serialisable.
    """
    payload: dict[str, Any] = json.loads(
        MessageRead.model_validate(message).model_dump_json()
    )
    return payload


def _event(name: str, data: dict[str, Any]) -> dict[str, str]:
    return {"event": name, "data": json.dumps(data)}
