from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import TimestampedBase

if TYPE_CHECKING:
    from econometrica.db.models.chat import Chat


class Project(TimestampedBase):
    """A workspace grouping related chats, datasets and analyses."""

    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_projects_name_not_blank"),
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)

    # Capability toggles. A chat may override web search and MCP; the code
    # sandbox is deliberately project-scoped only.
    web_search_enabled: Mapped[bool] = mapped_column(default=False)
    mcp_enabled: Mapped[bool] = mapped_column(default=False)
    code_sandbox_enabled: Mapped[bool] = mapped_column(default=False)

    validation_tier: Mapped[str] = mapped_column(String(20), default="critic")

    # ``{"planner": {"provider": ..., "model": ...}, ...}``
    model_assignments: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    chats: Mapped[list["Chat"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
