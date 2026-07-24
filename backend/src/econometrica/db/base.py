from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampedBase(Base):
    """Every domain entity carries an id and audit timestamps."""

    __abstract__ = True

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
