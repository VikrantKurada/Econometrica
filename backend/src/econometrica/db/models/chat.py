from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import TimestampedBase

if TYPE_CHECKING:
    from econometrica.db.models.message import Message
    from econometrica.db.models.project import Project


class Chat(TimestampedBase):
    """A conversation thread inside a project."""

    __tablename__ = "chats"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="ck_chats_name_not_blank"),)

    name: Mapped[str] = mapped_column(String(200))
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        index=True,
    )

    # Three-state toggles: None inherits the project setting, True/False override
    # it. Do not collapse None into False — they mean different things.
    web_search_enabled: Mapped[bool | None] = mapped_column(default=None)
    mcp_enabled: Mapped[bool | None] = mapped_column(default=None)

    project: Mapped["Project"] = relationship(back_populates="chats")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat",
        cascade="all, delete-orphan",
        # Ordered here so every load of a transcript is already correct;
        # created_at ties within a transaction, seq does not.
        order_by="Message.seq",
    )
