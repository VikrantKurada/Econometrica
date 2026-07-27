"""Writing confirmed observations into the store.

Kept out of the router because it is the one place that decides what "ingesting
the same file twice" means, and that is a data question rather than an HTTP one.

**Re-confirming replaces.** A user who realises they mapped a column wrongly
confirms again; leaving the first ingest behind would double every observation
and the second mapping would never take effect. So a re-ingest of the same
upload deletes the previous dataset and writes a new one — the blob is untouched
either way, which is what makes the correction cheap.
"""

from datetime import UTC, datetime
from uuid import UUID

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.db.models import Dataset, Observation
from econometrica.econ.fingerprint import fingerprint_frame
from econometrica.services.mapping import ColumnMapping, MappingError


def source_label(filename: str, when: datetime | None = None) -> str:
    """What reaches `DataQualityReport.source`.

    Names the file *and* when it was ingested, because a file re-uploaded after
    being corrected is a different series under the same name — the same reason
    a market adapter names its adjustment policy.
    """
    stamp = (when or datetime.now(UTC)).date()
    return f"upload: {filename} (ingested {stamp})"


async def ingest_observations(
    session: AsyncSession,
    *,
    project_id: UUID,
    upload_id: UUID,
    filename: str,
    blob_path: str,
    mapping: ColumnMapping,
    observations: pd.DataFrame,
) -> Dataset:
    """Store a confirmed mapping's observations, replacing any earlier ingest."""
    if not mapping.confirmed:
        raise MappingError(
            "this mapping has not been confirmed, so nothing may be stored from it"
        )

    # Addressed by the upload's own id so a second confirmation finds the first.
    existing = await session.scalars(
        select(Dataset).where(Dataset.blob_path == blob_path)
    )
    for previous in existing.all():
        await session.execute(
            delete(Observation).where(Observation.dataset_id == previous.id)
        )
        await session.delete(previous)
    await session.flush()

    dataset = Dataset(
        project_id=project_id,
        name=filename,
        filename=filename,
        blob_path=blob_path,
        source_label=source_label(filename),
        fingerprint=fingerprint_frame(observations),
        rows=len(observations),
        column_roles=dict(mapping.roles),
    )
    session.add(dataset)
    await session.flush()

    session.add_all(
        Observation(
            dataset_id=dataset.id,
            ts=pd.Timestamp(row.ts).tz_localize(UTC)
            if pd.Timestamp(row.ts).tzinfo is None
            else pd.Timestamp(row.ts),
            symbol=str(row.symbol),
            field=str(row.field),
            value=float(row.value),
        )
        for row in observations.itertuples()
    )
    await session.flush()
    return dataset


async def dataset_symbols(session: AsyncSession, dataset_id: UUID) -> list[str]:
    rows = await session.scalars(
        select(Observation.symbol).where(Observation.dataset_id == dataset_id).distinct()
    )
    return sorted(rows.all())
