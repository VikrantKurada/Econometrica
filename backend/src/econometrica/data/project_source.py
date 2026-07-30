"""A project's uploads, layered over whatever market source is configured.

Upload-first, and that ordering is the whole point: a run has to be able to mix
a file with fetched tickers, because the question this exists to answer
(correlate an uploaded index against a listed stock) needs both in one frame.
An all-or-nothing switch between market data and an upload would have been a
smaller change and could not answer it.

The composite records which source served each ticker, because only it knows.
`DataSteward` reads `provenance` back off it the same duck-typed way it already
reads `label`, so an ordinary source needs no changes to keep working.
"""

from datetime import date
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.data.base import PriceSource
from econometrica.data.uploaded import UploadedPriceSource
from econometrica.db.models import Dataset, Observation


class ProjectPriceSource:
    """Serves a project's uploaded symbols; delegates the rest."""

    def __init__(
        self,
        session: AsyncSession,
        market: PriceSource,
        *,
        uploads: dict[str, tuple[UUID, str]],
        label: str,
    ) -> None:
        self._session = session
        self._market = market
        self._uploads = uploads
        self._label = label
        self._provenance: dict[str, str] = {}

    @property
    def label(self) -> str:
        return self._label

    @property
    def provenance(self) -> dict[str, str]:
        """Which source served each ticker, filled in as they are served.

        A copy, because the Data Steward reads this after fetching and a
        mutable view of a live dict would let a later fetch rewrite a report
        that had already been built.
        """
        return dict(self._provenance)

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        entry = self._uploads.get(ticker)
        if entry is None:
            series = await self._market.prices(ticker, start=start, end=end)
            self._provenance[ticker] = _label_of(self._market)
            return series

        dataset_id, source_label = entry
        series = await UploadedPriceSource(
            self._session, dataset_id, label=source_label
        ).prices(ticker, start=start, end=end)
        self._provenance[ticker] = source_label
        return series


async def build_project_source(
    session: AsyncSession, project_id: UUID, *, market: PriceSource
) -> PriceSource:
    """Wrap ``market`` with the project's uploads, if it has any.

    One query, at construction, because `PriceSource.label` is a synchronous
    property and has to be composed before anything is fetched.

    `observations` carries no `project_id` — unlike `document_chunks`, which
    denormalises it — so this joins. That is affordable *here* because it is the
    only such query and it runs once per run; a second query site is the signal
    to denormalise instead.
    """
    rows = (
        await session.execute(
            # `created_at` is selected because it is ordered on: Postgres
            # refuses an ORDER BY expression that a SELECT DISTINCT does not
            # carry, since the sort key of a de-duplicated row is otherwise
            # undefined.
            select(
                Observation.symbol,
                Dataset.id,
                Dataset.source_label,
                Dataset.created_at,
            )
            .join(Dataset, Dataset.id == Observation.dataset_id)
            .where(Dataset.project_id == project_id)
            .distinct()
            .order_by(Dataset.created_at)
        )
    ).all()

    # Later rows overwrite earlier ones, so the most recently ingested dataset
    # wins a symbol two uploads both carry. Explicable to a user: the last file
    # you confirmed is the one in force.
    #
    # `created_at` is `func.now()`, which is *transaction start* — two datasets
    # written in one transaction tie exactly, the same reason `Message.seq`
    # exists. Two confirmations are two requests, so this is well-ordered in
    # use; only a test writing both at once has to set the column itself.
    uploads: dict[str, tuple[UUID, str]] = {
        symbol: (dataset_id, source_label) for symbol, dataset_id, source_label, _ in rows
    }
    if not uploads:
        # No wrapper at all rather than one that always delegates: a project
        # that never uploaded anything should not have its label changed or its
        # provenance described.
        return market

    labels = sorted({source_label for _, source_label in uploads.values()})
    noun = "dataset" if len(labels) == 1 else "datasets"
    return ProjectPriceSource(
        session,
        market,
        uploads=uploads,
        label=f"{_label_of(market)} + {len(labels)} uploaded {noun} ({', '.join(labels)})",
    )


def _label_of(source: PriceSource) -> str:
    """The same fallback `DataSteward` uses, so the two never disagree."""
    return getattr(source, "label", "") or type(source).__name__
