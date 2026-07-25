"""Tests for the Message model.

Ordering is the subtle part. ``created_at`` is the *transaction* timestamp in
Postgres, so several messages written in one transaction carry identical
timestamps — and a chat transcript rendered out of order is worse than useless.
The model therefore orders on a database-assigned identity column.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from econometrica.db.models import Chat, Message, Project


async def _chat(session) -> Chat:
    project = Project(name="P")
    chat = Chat(name="C", project=project)
    session.add(project)
    await session.flush()
    return chat


async def test_message_persists_with_defaults(session):
    chat = await _chat(session)
    message = Message(chat=chat, role="user", content="hello")
    session.add(message)
    await session.flush()

    assert message.id is not None
    assert message.seq is not None
    assert message.input_tokens == 0
    assert message.output_tokens == 0
    assert message.provider is None
    assert message.error is None


async def test_seq_orders_messages_written_in_one_transaction(session):
    """The case created_at cannot resolve."""
    chat = await _chat(session)
    contents = [f"m{i}" for i in range(5)]
    for content in contents:
        session.add(Message(chat=chat, role="user", content=content))
    await session.flush()

    rows = await session.scalars(
        select(Message).where(Message.chat_id == chat.id).order_by(Message.seq)
    )
    assert [m.content for m in rows] == contents


async def test_seq_is_monotonic_across_chats(session):
    chat_a = await _chat(session)
    chat_b = await _chat(session)
    first = Message(chat=chat_a, role="user", content="a")
    second = Message(chat=chat_b, role="user", content="b")
    session.add_all([first, second])
    await session.flush()

    assert second.seq > first.seq


async def test_assistant_messages_record_provenance(session):
    chat = await _chat(session)
    message = Message(
        chat=chat,
        role="assistant",
        content="the beta is 1.3",
        provider="ollama",
        model="llama3.2:latest",
        input_tokens=26,
        output_tokens=8,
        latency_ms=412.5,
        stop_reason="stop",
    )
    session.add(message)
    await session.flush()

    assert message.provider == "ollama"
    assert message.input_tokens == 26
    assert message.latency_ms == pytest.approx(412.5)


async def test_messages_cascade_when_the_chat_is_deleted(session):
    chat = await _chat(session)
    session.add(Message(chat=chat, role="user", content="hi"))
    await session.flush()

    await session.delete(chat)
    await session.flush()

    remaining = await session.scalars(select(Message))
    assert remaining.all() == []


async def test_role_is_constrained_at_the_database_level(session):
    """The API validates roles, but a raw insert must not smuggle one past."""
    chat = await _chat(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                "INSERT INTO messages (id, chat_id, role, content) "
                "VALUES (gen_random_uuid(), :chat_id, 'banana', 'x')"
            ),
            {"chat_id": chat.id},
        )


async def test_content_may_be_empty_for_a_failed_generation(session):
    """A provider failure still persists a row so the error is visible."""
    chat = await _chat(session)
    message = Message(
        chat=chat, role="assistant", content="", error="ollama: daemon not running"
    )
    session.add(message)
    await session.flush()

    assert message.error
