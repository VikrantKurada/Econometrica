from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampedBase(Base):
    """Every domain entity carries an id and audit timestamps."""

    __abstract__ = True

    # ``updated_at`` is computed by the server, so after an UPDATE SQLAlchemy
    # would normally expire it and reload it on next access — a lazy IO that
    # raises MissingGreenlet under asyncio. Fetching it eagerly via RETURNING
    # keeps the instance complete without a second round trip.
    # RUF012 wants ClassVar here, but SQLAlchemy declares __mapper_args__ as an
    # instance variable, so ClassVar is a mypy error. The dict is never mutated.
    __mapper_args__: dict[str, Any] = {"eager_defaults": True}  # noqa: RUF012

    # ``default`` covers ORM inserts, ``server_default`` covers everything else
    # (seed scripts, data migrations, psql). ``gen_random_uuid()`` is built into
    # PostgreSQL 13+, so no extension is required.
    id: Mapped[UUID] = mapped_column(
        primary_key=True, default=uuid4, server_default=text("gen_random_uuid()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
