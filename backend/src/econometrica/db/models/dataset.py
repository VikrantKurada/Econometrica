"""Uploaded data: what it is, and every observation it carries.

Two tables, split because they answer different questions at very different
row counts. A `Dataset` is one uploaded file — its name, the mapping that was
confirmed for it, and where its retained blob lives. `Observation` is the data,
long-format, and it is **the project's first Timescale hypertable**.

**Long format, not wide.** One row per (dataset, ts, symbol, field), which is
what a panel of any width flattens to. A wide file's header names the symbol; a
long file's ticker column does. Storing either shape verbatim would make the
schema depend on the upload, and no query could span two files.

**`field` is part of the key.** A wide file mapping both a close and a volume
produces two rows for the same date and symbol, so a key of (dataset, ts,
symbol) would refuse an ordinary file. It is also required to include the
partitioning column: Timescale rejects a unique constraint that omits it.

**The blob is referenced, not stored.** §9 retains the original file, and a
hypertable is for observations — a CSV is not one. The row points at a path
under the storage directory.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import Base, TimestampedBase

#: The roles an observation may carry, matching `services/ingest.Role` minus
#: the ones that describe a column rather than a value. A typo reaching the
#: table would produce a field no tool binds to, silently.
OBSERVATION_FIELDS = ("price", "return", "volume", "factor")


class Dataset(TimestampedBase):
    __tablename__ = "datasets"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_datasets_name_not_blank"),
        CheckConstraint("rows >= 0", name="ck_datasets_rows_not_negative"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(500))
    #: Relative to the storage directory, so moving the directory does not
    #: invalidate every row.
    blob_path: Mapped[str] = mapped_column(String(1000))
    #: What reaches `DataQualityReport.source`. Names the file and when it was
    #: ingested, because which upload produced a number is part of reproducing
    #: it — the same reason a market adapter names its adjustment policy.
    source_label: Mapped[str] = mapped_column(String(300))
    #: Of the observations, so a result can be tied back to this ingest.
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    rows: Mapped[int] = mapped_column(Integer, default=0)
    #: The confirmed mapping. Provenance: without it nobody can say why a
    #: column became a price, and a re-ingest could not reproduce these rows.
    column_roles: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )

    observations: Mapped[list["Observation"]] = relationship(
        back_populates="dataset", cascade="all, delete-orphan", passive_deletes=True
    )


class Observation(Base):
    """One value, for one symbol, at one instant.

    No surrogate id: the composite key *is* the identity, and a hypertable with
    a synthetic primary key would carry an index nothing queries by.
    """

    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint(
            "field IN ('price', 'return', 'volume', 'factor')",
            name="ck_observations_field_known",
        ),
    )

    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(64), primary_key=True)
    field: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[float] = mapped_column(Float)

    dataset: Mapped["Dataset"] = relationship(back_populates="observations")


#: Declared rather than inherited. `create_hypertable` makes its own descending
#: index on the time column, which nothing in the models knows about — so
#: `alembic check` saw an index it had not been told to create and wanted to
#: drop it, on every run. Creating it ourselves and telling Timescale not to
#: bother keeps the schema fully described by the metadata, which is the only
#: way `alembic check` can mean anything here.
Index("ix_observations_ts", Observation.__table__.c.ts.desc())


# `create_all` builds an ordinary table, and an ordinary table behaves
# identically until the row counts get interesting — so the conversion is
# attached to table creation rather than left to the migration alone. The
# migration issues the same call for a real database; this covers the test
# database, which is built from the metadata.
event.listen(
    Observation.__table__,
    "after_create",
    # `DDL` is untyped in SQLAlchemy's stubs, hence the ignore; the argument is
    # a plain string and there is nothing here for mypy to check anyway.
    DDL(  # type: ignore[no-untyped-call]
        "SELECT create_hypertable('observations', 'ts',"
        " if_not_exists => TRUE, create_default_indexes => FALSE)"
    ).execute_if(dialect="postgresql"),
)
