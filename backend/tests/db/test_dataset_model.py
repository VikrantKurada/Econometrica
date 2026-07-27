"""The dataset store: a description, its observations, and its retained blob.

The first hypertable in the project. That is worth its own tests rather than
trust, because `alembic check` cannot see a hypertable conversion at all — a
table that is merely a table looks identical to it — and a plain table would
work perfectly until the row counts got interesting.

Observations are long-format on purpose: one row per (dataset, ts, symbol,
field), which is what a panel of any width flattens to and what a hypertable
partitions well.
"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from econometrica.db.models import Dataset, Observation, Project


async def make_dataset(session, **overrides) -> Dataset:
    project = Project(name="Uploads")
    session.add(project)
    await session.flush()

    fields = {
        "project_id": project.id,
        "name": "prices.csv",
        "filename": "prices.csv",
        "blob_path": "uploads/abc/original",
        "source_label": "upload: prices.csv",
        "fingerprint": "f" * 64,
        "rows": 2,
        "column_roles": {"date": "date", "AAPL": "price"},
    }
    fields.update(overrides)
    dataset = Dataset(**fields)
    session.add(dataset)
    await session.flush()
    return dataset


def observation(dataset, *, ts="2024-01-02", symbol="AAPL", field="price", value=100.0):
    import pandas as pd

    return Observation(
        dataset_id=dataset.id,
        ts=pd.Timestamp(ts, tz="UTC").to_pydatetime(),
        symbol=symbol,
        field=field,
        value=value,
    )


# --- the hypertable ----------------------------------------------------------


async def test_observations_is_a_hypertable_and_an_ordinary_table_is_not(session):
    """Asserted against Timescale's own catalogue, not against the DDL we
    wrote. A `create_hypertable` that silently no-opped would leave a table
    that behaves correctly and scales like an ordinary one.

    `projects` is checked alongside it so the query is visibly discriminating:
    a view that returned every table would pass the first assertion on its own.
    """
    listed = await session.scalars(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables"
            " WHERE hypertable_name IN ('observations', 'projects')"
        )
    )

    assert listed.all() == ["observations"]


async def test_the_hypertable_partitions_on_time(session):
    result = await session.execute(
        text(
            "SELECT column_name FROM timescaledb_information.dimensions"
            " WHERE hypertable_name = 'observations' AND dimension_number = 1"
        )
    )

    assert result.scalar_one() == "ts"


async def test_inserting_observations_creates_a_chunk(session):
    """A hypertable with no chunks is indistinguishable from a plain table, so
    this is what proves the partitioning is live rather than declared."""
    dataset = await make_dataset(session)
    session.add(observation(dataset))
    await session.flush()

    result = await session.execute(
        text(
            "SELECT count(*) FROM timescaledb_information.chunks"
            " WHERE hypertable_name = 'observations'"
        )
    )

    assert result.scalar_one() >= 1


# --- round trip ---------------------------------------------------------------


async def test_a_long_format_dataset_round_trips(session):
    dataset = await make_dataset(session)
    session.add_all(
        [
            observation(dataset, ts="2024-01-02", symbol="AAPL", value=100.0),
            observation(dataset, ts="2024-01-02", symbol="MSFT", value=200.0),
            observation(dataset, ts="2024-01-03", symbol="AAPL", value=101.0),
        ]
    )
    await session.flush()

    rows = (
        await session.scalars(
            select(Observation).where(Observation.dataset_id == dataset.id)
        )
    ).all()

    assert len(rows) == 3
    assert {row.symbol for row in rows} == {"AAPL", "MSFT"}
    assert sorted(row.value for row in rows) == [100.0, 101.0, 200.0]


async def test_one_symbol_can_carry_several_fields_at_the_same_instant(session):
    """A wide file mapping both a close and a volume produces two rows for the
    same date and symbol, so `field` has to be part of the key. The plan said
    (dataset, ts, symbol); that would have refused a perfectly ordinary file."""
    dataset = await make_dataset(session)
    session.add_all(
        [
            observation(dataset, field="price", value=100.0),
            observation(dataset, field="volume", value=1_000_000.0),
        ]
    )

    await session.flush()

    rows = (await session.scalars(select(Observation))).all()
    assert len(rows) == 2


async def test_the_same_observation_twice_is_refused(session):
    dataset = await make_dataset(session)
    session.add(observation(dataset))
    await session.flush()

    session.add(observation(dataset, value=999.0))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_an_unknown_field_is_refused(session):
    """`field` names a role from a closed vocabulary. A typo that reached the
    table would silently produce a column no tool binds to."""
    dataset = await make_dataset(session)
    session.add(observation(dataset, field="sentiment"))

    with pytest.raises(IntegrityError):
        await session.flush()


# --- lifecycle ----------------------------------------------------------------


async def test_deleting_a_dataset_takes_its_observations(session):
    dataset = await make_dataset(session)
    session.add(observation(dataset))
    await session.flush()

    await session.delete(dataset)
    await session.flush()

    assert (await session.scalars(select(Observation))).all() == []


async def test_deleting_a_project_takes_its_datasets(session):
    dataset = await make_dataset(session)
    project = await session.get(Project, dataset.project_id)

    await session.delete(project)
    await session.flush()

    assert (await session.scalars(select(Dataset))).all() == []


async def test_a_dataset_records_where_its_blob_is(session):
    """§9 retains the original file. The row points at it rather than holding
    it: a hypertable is for observations, and a CSV is not one."""
    dataset = await make_dataset(session, blob_path="uploads/xyz/original")

    assert dataset.blob_path == "uploads/xyz/original"


async def test_a_dataset_records_the_roles_that_produced_it(session):
    """The mapping is provenance. Without it nobody can say why a column became
    a price, and a re-ingest could not reproduce the same observations."""
    dataset = await make_dataset(session, column_roles={"date": "date", "v": "volume"})

    fetched = await session.get(Dataset, dataset.id)

    assert fetched.column_roles == {"date": "date", "v": "volume"}


async def test_a_blank_name_is_refused(session):
    project = Project(name="P")
    session.add(project)
    await session.flush()

    session.add(
        Dataset(
            project_id=project.id,
            name="   ",
            filename="f.csv",
            blob_path="p",
            source_label="upload: f.csv",
            fingerprint="a" * 64,
            rows=1,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_a_negative_row_count_is_refused(session):
    project = Project(name="P")
    session.add(project)
    await session.flush()

    session.add(
        Dataset(
            project_id=project.id,
            name="f.csv",
            filename="f.csv",
            blob_path="p",
            source_label="upload: f.csv",
            fingerprint="a" * 64,
            rows=-1,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
