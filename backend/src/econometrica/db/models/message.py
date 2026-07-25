"""One turn in a conversation.

Assistant rows carry their own provenance — which provider and model produced
them, what they cost, how long they took. That is deliberate: the project may
reassign a role to a different model mid-conversation, so provenance recorded
on the chat or the project could not explain any individual reply. It is also
the row the Phase 4 trace and the telemetry dashboard both read from.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Float, ForeignKey, Identity, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import TimestampedBase

if TYPE_CHECKING:
    from econometrica.db.models.chat import Chat

ROLES = ("system", "user", "assistant")


class Message(TimestampedBase):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('system', 'user', 'assistant')", name="ck_messages_role_known"
        ),
    )

    #: Ordering key. ``created_at`` is the *transaction* timestamp in Postgres,
    #: so messages written in one transaction tie exactly and a transcript
    #: sorted on it can come back scrambled. A database-assigned identity is
    #: monotonic and race-free without a per-chat MAX() query.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, index=True)

    chat_id: Mapped[UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    #: Empty on a failed generation — the row still exists so the failure is
    #: visible in the transcript rather than silently absent.
    content: Mapped[str] = mapped_column(Text, default="", server_default="")

    # Provenance and cost, set on assistant turns only.
    provider: Mapped[str | None] = mapped_column(String(50), default=None)
    model: Mapped[str | None] = mapped_column(String(200), default=None)
    input_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    stop_reason: Mapped[str | None] = mapped_column(String(50), default=None)
    #: Populated when generation failed. Mutually exclusive with useful content.
    error: Mapped[str | None] = mapped_column(Text, default=None)

    chat: Mapped["Chat"] = relationship(back_populates="messages")
