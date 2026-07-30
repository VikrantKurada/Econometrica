"""A project's uploads layered over a market source.

The fake market source is deliberately not a mock: these tests are about which
source answers, so the thing standing in for Yahoo has to be able to answer, to
refuse, and to say which it did.
"""

from datetime import UTC, date, datetime

import pandas as pd
import pytest

from econometrica.data.base import DataUnavailableError
from econometrica.data.project_source import ProjectPriceSource, build_project_source
from econometrica.db.models import Dataset, Observation, Project

START, END = date(2024, 1, 1), date(2024, 1, 31)


class FakeMarket:
    """Answers for the symbols it was given, refuses everything else."""

    label = "Fake Market (dividend-adjusted)"

    def __init__(self, symbols: set[str]) -> None:
        self._symbols = symbols
        self.asked: list[str] = []

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        self.asked.append(ticker)
        if ticker not in self._symbols:
            raise DataUnavailableError(f"{ticker}: not listed")
        index = pd.to_datetime(["2024-01-02", "2024-01-03"])
        return pd.Series([1.0, 2.0], index=index, name=ticker)


async def make_project(session, name="Uploads") -> Project:
    project = Project(name=name)
    session.add(project)
    await session.flush()
    return project


async def make_dataset(session, *, project_id, name, rows, created_at=None) -> Dataset:
    dataset = Dataset(
        project_id=project_id,
        name=name,
        filename=name,
        blob_path=f"uploads/{name}",
        source_label=f"upload: {name} (ingested 2024-01-05)",
        fingerprint="a" * 64,
        rows=len(rows),
        column_roles={"date": "date"},
        **({"created_at": created_at} if created_at else {}),
    )
    session.add(dataset)
    await session.flush()

    session.add_all(
        Observation(
            dataset_id=dataset.id,
            ts=pd.Timestamp(ts, tz="UTC").to_pydatetime(),
            symbol=symbol,
            field="price",
            value=value,
        )
        for ts, symbol, value in rows
    )
    await session.flush()
    return dataset


LONDON = [("2024-01-02", "LONDON", 500.0), ("2024-01-03", "LONDON", 501.0)]


async def test_a_symbol_an_upload_carries_is_served_from_it(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)
    series = await source.prices("LONDON", start=START, end=END)

    assert list(series) == [500.0, 501.0]
    # The market source was never asked: an upload-only run must not need it.
    assert market.asked == []


async def test_a_symbol_no_upload_carries_falls_through_to_the_market(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)
    series = await source.prices("GOOGL", start=START, end=END)

    assert list(series) == [1.0, 2.0]
    assert market.asked == ["GOOGL"]


async def test_provenance_names_the_source_of_every_served_ticker(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)
    await source.prices("LONDON", start=START, end=END)
    await source.prices("GOOGL", start=START, end=END)

    assert source.provenance == {
        "LONDON": "upload: hpi.csv (ingested 2024-01-05)",
        "GOOGL": "Fake Market (dividend-adjusted)",
    }


async def test_the_newest_dataset_wins_a_symbol_two_uploads_carry(session):
    project = await make_project(session)
    # `created_at` defaults to `func.now()`, which in Postgres is *transaction
    # start* — two datasets flushed in one transaction tie exactly. That is the
    # same trap that makes `Message.seq` exist. Two confirmations are two
    # requests in real use, so the test has to say so explicitly or it asserts
    # a coin flip that happens to land right today.
    await make_dataset(
        session,
        project_id=project.id,
        name="old.csv",
        rows=[("2024-01-02", "LONDON", 1.0)],
        created_at=datetime(2024, 1, 5, tzinfo=UTC),
    )
    await make_dataset(
        session,
        project_id=project.id,
        name="new.csv",
        rows=[("2024-01-02", "LONDON", 99.0)],
        created_at=datetime(2024, 2, 5, tzinfo=UTC),
    )

    source = await build_project_source(session, project.id, market=FakeMarket(set()))
    series = await source.prices("LONDON", start=START, end=END)

    assert list(series) == [99.0]


async def test_another_projects_upload_is_not_visible(session):
    mine = await make_project(session, name="Mine")
    theirs = await make_project(session, name="Theirs")
    await make_dataset(session, project_id=theirs.id, name="hpi.csv", rows=LONDON)
    market = FakeMarket(set())

    source = await build_project_source(session, mine.id, market=market)
    with pytest.raises(DataUnavailableError):
        await source.prices("LONDON", start=START, end=END)
    assert market.asked == ["LONDON"]


async def test_the_label_names_the_market_source_and_every_upload(session):
    project = await make_project(session)
    await make_dataset(session, project_id=project.id, name="hpi.csv", rows=LONDON)

    source = await build_project_source(session, project.id, market=FakeMarket(set()))

    assert isinstance(source, ProjectPriceSource)
    assert source.label == (
        "Fake Market (dividend-adjusted)"
        " + 1 uploaded dataset (upload: hpi.csv (ingested 2024-01-05))"
    )


async def test_a_project_with_no_uploads_is_the_market_source_unchanged(session):
    project = await make_project(session)
    market = FakeMarket({"GOOGL"})

    source = await build_project_source(session, project.id, market=market)

    # Not merely equivalent — the same object, so a project that never uploaded
    # anything carries no wrapper, no query cost and no changed label.
    assert source is market
