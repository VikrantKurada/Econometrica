from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, String, Text, false, text
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
        # The API rejects other values, but resolve_capabilities feeds this
        # straight to the orchestrator — an out-of-band write must not be able
        # to smuggle an unknown tier past it.
        CheckConstraint(
            "validation_tier IN ('single', 'critic', 'consensus')",
            name="ck_projects_validation_tier_known",
        ),
    )

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)

    # Capability toggles. A chat may override web search and MCP; the code
    # sandbox is deliberately project-scoped only.
    #
    # Each NOT NULL column pairs its Python-side ``default`` with a matching
    # ``server_default`` so that inserts which bypass the ORM still land valid
    # rows. The two must always express the same value.
    web_search_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    mcp_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
    code_sandbox_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())

    #: MCP servers this project may connect to, and the tools it may call on
    #: them. Both default empty: §9 has MCP off by default, and turning the
    #: capability on is not consent to whatever a server offers — every tool is
    #: named one at a time. See `mcp/allowlist.py`.
    mcp_servers: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    mcp_allowlist: Mapped[list[Any]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )

    validation_tier: Mapped[str] = mapped_column(
        String(20), default="critic", server_default="critic"
    )

    # ``{"planner": {"provider": ..., "model": ...}, ...}``
    model_assignments: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    chats: Mapped[list["Chat"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
