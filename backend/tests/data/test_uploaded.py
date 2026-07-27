"""An ingested upload, served through the same protocol as a fetched ticker.

This is the point of the whole upload path. Above `PriceSource` nothing knows
or cares where a series came from — the Data Steward aligns it, the tools run on
it, the manifest fingerprints it — so an uploaded file becomes analysable by
satisfying one small interface rather than by teaching anything upstream about
uploads.

Which means the honesty rules apply here too: the label says which file and when
it was ingested, and it must not contain the word `synthetic`.
"""

from datetime import date

import pytest

from econometrica.data.base import DataUnavailableError
from econometrica.data.uploaded import UploadedPriceSource
from econometrica.db.models import Dataset, Observation, Project

START, END = date(2024, 1, 1), date(2024, 1, 31)


async def ingest(session, rows, *, name="prices.csv", roles=None) -> Dataset:
    import pandas as pd

    project = Project(name="Uploads")
    session.add(project)
    await session.flush()

    dataset = Dataset(
        project_id=project.id,
        name=name,
        filename=name,
        blob_path=f"uploads/{name}",
        source_label=f"upload: {name} (2024-01-05)",
        fingerprint="a" * 64,
        rows=len(rows),
        column_roles=roles or {"date": "date", "AAPL": "price"},
    )
    session.add(dataset)
    await session.flush()

    session.add_all(
        Observation(
            dataset_id=dataset.id,
            ts=pd.Timestamp(ts, tz="UTC").to_pydatetime(),
            symbol=symbol,
            field=field,
            value=value,
        )
        for ts, symbol, field, value in rows
    )
    await session.flush()
    return dataset


PRICES = [
    ("2024-01-02", "AAPL", "price", 100.0),
    ("2024-01-03", "AAPL", "price", 101.0),
    ("2024-01-04", "AAPL", "price", 102.0),
    ("2024-01-02", "MSFT", "price", 200.0),
    ("2024-01-03", "MSFT", "price", 201.0),
]


# --- serving a series ---------------------------------------------------------


async def test_an_ingested_symbol_comes_back_as_a_series(session):
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    series = await source.prices("AAPL", start=START, end=END)

    assert list(series) == [100.0, 101.0, 102.0]
    assert series.name == "AAPL"


async def test_the_series_is_indexed_by_date_and_sorted(session):
    import pandas as pd

    dataset = await ingest(session, list(reversed(PRICES)))
    source = UploadedPriceSource(session, dataset.id)

    series = await source.prices("AAPL", start=START, end=END)

    assert isinstance(series.index, pd.DatetimeIndex)
    assert series.index.is_monotonic_increasing


async def test_the_index_is_naive_like_every_other_source(session):
    """The Data Steward windows against a naive `pd.Timestamp`, and comparing
    that to a tz-aware index raises. Stored as timestamptz, served naive."""
    import pandas as pd

    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    series = await source.prices("AAPL", start=START, end=END)

    assert pd.DatetimeIndex(series.index).tz is None


async def test_only_the_requested_window_comes_back(session):
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    # A single-day window is refused as empty by every source here, so the
    # narrowest real window is two days — enough to leave the 2024-01-02
    # observation outside it.
    series = await source.prices("AAPL", start=date(2024, 1, 3), end=date(2024, 1, 4))

    assert list(series) == [101.0, 102.0]


async def test_symbols_do_not_leak_into_one_another(session):
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    assert list(await source.prices("MSFT", start=START, end=END)) == [200.0, 201.0]


async def test_a_dataset_serves_only_its_own_observations(session):
    """Two uploads in one project must not see each other's rows — the whole
    reason a dataset id scopes every query."""
    first = await ingest(session, PRICES, name="a.csv")
    second = await ingest(
        session, [("2024-01-02", "AAPL", "price", 999.0)], name="b.csv"
    )

    series = await UploadedPriceSource(session, second.id).prices(
        "AAPL", start=START, end=END
    )

    assert list(series) == [999.0]
    assert first.id != second.id


# --- which field ---------------------------------------------------------------


async def test_prices_are_served_in_preference_to_other_fields(session):
    """A file mapping both a close and a volume has two fields for the same
    symbol; `prices` means the price."""
    dataset = await ingest(
        session,
        [
            ("2024-01-02", "AAPL", "price", 100.0),
            ("2024-01-02", "AAPL", "volume", 5_000_000.0),
        ],
    )
    source = UploadedPriceSource(session, dataset.id)

    assert list(await source.prices("AAPL", start=START, end=END)) == [100.0]


async def test_a_returns_only_upload_still_resolves(session):
    """Not every file carries levels. A returns column is what it is, and
    refusing it would make a legitimate upload unusable."""
    dataset = await ingest(
        session,
        [
            ("2024-01-02", "AAPL", "return", 0.01),
            ("2024-01-03", "AAPL", "return", -0.02),
        ],
        roles={"date": "date", "AAPL": "return"},
    )
    source = UploadedPriceSource(session, dataset.id)

    assert list(await source.prices("AAPL", start=START, end=END)) == [0.01, -0.02]


# --- refusals -----------------------------------------------------------------


async def test_an_unknown_symbol_is_refused_by_name(session):
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    with pytest.raises(DataUnavailableError, match="GOOG"):
        await source.prices("GOOG", start=START, end=END)


async def test_a_window_with_no_observations_is_refused(session):
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    with pytest.raises(DataUnavailableError, match="AAPL"):
        await source.prices("AAPL", start=date(2030, 1, 1), end=date(2030, 12, 31))


async def test_a_backwards_window_is_refused(session):
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id)

    with pytest.raises(ValueError, match="window"):
        await source.prices("AAPL", start=END, end=START)


# --- what it says about itself -------------------------------------------------


async def test_the_label_names_the_file(session):
    """`DataQualityReport.source` has to say which upload a number came from,
    for the same reason a market adapter names its adjustment policy."""
    dataset = await ingest(session, PRICES, name="factors.csv")
    source = UploadedPriceSource(session, dataset.id, label=dataset.source_label)

    assert "factors.csv" in source.label


async def test_the_label_does_not_claim_to_be_synthetic(session):
    """The Data Steward's `synthetic_data` risk flag fires on a substring
    match. A user's own data must never be reported as generated."""
    dataset = await ingest(session, PRICES)
    source = UploadedPriceSource(session, dataset.id, label=dataset.source_label)

    assert "synthetic" not in source.label.lower()


async def test_it_satisfies_the_price_source_protocol(session):
    from econometrica.data.base import PriceSource

    dataset = await ingest(session, PRICES)

    assert isinstance(UploadedPriceSource(session, dataset.id), PriceSource)


# --- through the Data Steward ---------------------------------------------------


async def test_an_upload_resolves_through_the_data_steward(session):
    """The composition that makes an upload worth having: it goes through the
    same steward, gets the same alignment and the same quality report as a
    fetched ticker, and nothing above the protocol had to learn about uploads.
    """
    from econometrica.agents.data_steward import DataSteward
    from econometrica.agents.schemas import DatasetSpec

    rows = [
        (f"2024-01-{day:02d}", symbol, "price", base + day)
        for day in range(1, 20)
        for symbol, base in (("AAPL", 100.0), ("MSFT", 200.0))
    ]
    dataset = await ingest(session, rows)
    source = UploadedPriceSource(session, dataset.id, label=dataset.source_label)

    resolved = await DataSteward(source, min_obs=5).resolve(
        DatasetSpec(tickers=["AAPL", "MSFT"], start=START, end=END)
    )

    assert list(resolved.prices.columns) == ["AAPL", "MSFT"]
    assert resolved.report.rows == 19
    assert resolved.report.source == dataset.source_label
    assert not resolved.report.has("synthetic_data")
    assert resolved.report.fingerprint
