"""The on-disk price cache.

Two things it must never do, and both have tests here rather than comments: it
must not change the numbers a source would have returned, and it must not
quietly stand in for a source that is unreachable. The second is the subtle
one — an adjusted close is recomputed by the vendor every time a split or a
dividend happens, so a cache entry served long enough after it was written is a
*different series*, and a re-run that "reproduced" from it would be reporting
on the cache rather than on the data.
"""

import os
from datetime import date, timedelta

import pandas as pd
import pytest

from econometrica.agents.data_steward import DataUnavailableError
from econometrica.data.cache import CachingPriceSource

START, END = date(2024, 1, 1), date(2024, 1, 31)


class CountingSource:
    """A fake upstream that records every fetch and can be made to fail."""

    label = "counting fake (unadjusted close)"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, date, date]] = []
        self.error = error

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        self.calls.append((ticker, start, end))
        if self.error is not None:
            raise self.error
        index = pd.date_range(start, end, freq="D")
        return pd.Series(
            [100.0 + i for i in range(len(index))], index=index, name=ticker, dtype=float
        )


def cache(tmp_path, source, *, namespace: str = "counting", **kwargs) -> CachingPriceSource:
    return CachingPriceSource(source, namespace=namespace, root=tmp_path, **kwargs)


# --- hits and misses ---------------------------------------------------------


async def test_a_second_identical_fetch_is_served_from_disk(tmp_path):
    source = CountingSource()
    cached = cache(tmp_path, source)

    first = await cached.prices("AAPL", start=START, end=END)
    second = await cached.prices("AAPL", start=START, end=END)

    assert len(source.calls) == 1
    assert first.equals(second)


async def test_the_cached_series_round_trips_exactly(tmp_path):
    """A cache that perturbs a value changes the data fingerprint, and every
    manifest written over it would describe data nothing can reproduce."""
    source = CountingSource()
    cached = cache(tmp_path, source)

    direct = await CountingSource().prices("AAPL", start=START, end=END)
    await cached.prices("AAPL", start=START, end=END)
    from_disk = await cached.prices("AAPL", start=START, end=END)

    assert from_disk.equals(direct)
    assert from_disk.name == direct.name
    assert from_disk.dtype == direct.dtype


async def test_a_sub_window_is_served_from_a_cached_superset(tmp_path):
    """A study of 2020-2023 and a study of 2021-2022 should not be two fetches."""
    source = CountingSource()
    cached = cache(tmp_path, source)

    whole = await cached.prices("AAPL", start=START, end=END)
    inner_start, inner_end = date(2024, 1, 10), date(2024, 1, 20)
    inner = await cached.prices("AAPL", start=inner_start, end=inner_end)

    assert len(source.calls) == 1
    assert inner.equals(whole.loc[str(inner_start) : str(inner_end)])


async def test_a_window_reaching_past_the_cached_one_refetches(tmp_path):
    source = CountingSource()
    cached = cache(tmp_path, source)

    await cached.prices("AAPL", start=START, end=END)
    await cached.prices("AAPL", start=START, end=date(2024, 3, 1))

    assert len(source.calls) == 2


async def test_the_requested_window_is_what_is_recorded_not_the_data_it_returned(tmp_path):
    """A request for a window starting on a Sunday gets data from the Monday.
    Recording the data's own span instead would make that request permanently
    uncacheable — every repeat would look like it reached past what was
    stored."""
    source = CountingSource()
    cached = cache(tmp_path, source)

    await cached.prices("AAPL", start=START, end=END)
    await cached.prices("AAPL", start=START, end=END)

    assert len(source.calls) == 1


# --- keys --------------------------------------------------------------------


async def test_different_namespaces_do_not_collide(tmp_path):
    yahoo, other = CountingSource(), CountingSource()

    await cache(tmp_path, yahoo, namespace="yahoo").prices("AAPL", start=START, end=END)
    await cache(tmp_path, other, namespace="other").prices("AAPL", start=START, end=END)

    assert len(yahoo.calls) == 1
    assert len(other.calls) == 1


@pytest.mark.parametrize("ticker", ["^GSPC", "EURUSD=X", "BRK-B", "AAPL.US", "BTC-USD"])
async def test_a_ticker_the_filesystem_would_reject_is_still_cacheable(tmp_path, ticker):
    """Real symbols carry ^, = and . — none of which may reach a path
    unexamined on Windows."""
    source = CountingSource()
    cached = cache(tmp_path, source)

    await cached.prices(ticker, start=START, end=END)
    served = await cached.prices(ticker, start=START, end=END)

    assert len(source.calls) == 1
    assert served.name == ticker


async def test_tickers_that_sanitise_to_the_same_name_do_not_collide(tmp_path):
    """`^GSPC` and `_GSPC` are different symbols; if sanitising is all that
    distinguishes their cache paths, one would serve the other's prices."""
    source = CountingSource()
    cached = cache(tmp_path, source)

    await cached.prices("^GSPC", start=START, end=END)
    await cached.prices("_GSPC", start=START, end=END)

    assert len(source.calls) == 2


# --- damage ------------------------------------------------------------------


async def test_a_corrupt_cache_file_is_refetched_rather_than_raised(tmp_path):
    """A cache that can break the application is worse than no cache."""
    source = CountingSource()
    cached = cache(tmp_path, source)
    await cached.prices("AAPL", start=START, end=END)

    for path in tmp_path.rglob("*.parquet"):
        path.write_bytes(b"not parquet")

    served = await cached.prices("AAPL", start=START, end=END)

    assert len(source.calls) == 2
    assert len(served) > 0


async def test_a_failed_fetch_leaves_nothing_behind(tmp_path):
    source = CountingSource(error=DataUnavailableError("upstream is down"))
    cached = cache(tmp_path, source)

    with pytest.raises(DataUnavailableError):
        await cached.prices("AAPL", start=START, end=END)

    assert list(tmp_path.rglob("*.parquet")) == []


# --- being offline -----------------------------------------------------------


async def test_a_cached_window_is_served_while_the_source_is_unreachable(tmp_path):
    """This is what offline-friendly means here: work already fetched stays
    usable, so a run, its re-run and its exports do not each need the network."""
    source = CountingSource()
    cached = cache(tmp_path, source)
    await cached.prices("AAPL", start=START, end=END)

    source.error = OSError("network unreachable")
    served = await cached.prices("AAPL", start=START, end=END)

    assert len(served) > 0


async def test_an_unreachable_source_with_nothing_cached_names_the_source(tmp_path):
    source = CountingSource(error=OSError("network unreachable"))
    cached = cache(tmp_path, source)

    with pytest.raises(DataUnavailableError, match="counting fake"):
        await cached.prices("AAPL", start=START, end=END)


# --- staleness ---------------------------------------------------------------


def age(tmp_path, days: int) -> None:
    """Backdate every cache file, so expiry is tested against real mtimes."""
    for path in tmp_path.rglob("*.parquet"):
        old = path.stat().st_mtime - days * 86400
        os.utime(path, (old, old))


async def test_an_expired_entry_is_refetched(tmp_path):
    source = CountingSource()
    cached = cache(tmp_path, source, max_age=timedelta(days=1))
    await cached.prices("AAPL", start=START, end=END)

    age(tmp_path, days=3)
    await cached.prices("AAPL", start=START, end=END)

    assert len(source.calls) == 2


async def test_an_expired_entry_is_refused_rather_than_served_when_the_source_is_down(tmp_path):
    """Serving it would be the one failure this cache must not have. A vendor
    recomputes adjusted closes on every split and dividend, so a stale entry is
    a different series — and a re-run that reproduced from it would be
    reporting on the cache, not on the data."""
    source = CountingSource()
    cached = cache(tmp_path, source, max_age=timedelta(days=1))
    await cached.prices("AAPL", start=START, end=END)

    age(tmp_path, days=3)
    source.error = OSError("network unreachable")

    with pytest.raises(DataUnavailableError, match="stale"):
        await cached.prices("AAPL", start=START, end=END)


# --- transparency ------------------------------------------------------------


async def test_the_label_is_the_wrapped_sources_own(tmp_path):
    """`label` becomes `DataQualityReport.source`. Whether a fetch happened to
    hit the disk is not a fact about where the prices came from, and putting it
    in the report would make two identical analyses read differently."""
    source = CountingSource()

    assert cache(tmp_path, source).label == source.label


async def test_a_backwards_window_is_refused_by_the_wrapped_source(tmp_path):
    """The cache must not swallow validation the source performs."""
    source = CountingSource(error=ValueError("the requested window is empty"))
    cached = cache(tmp_path, source)

    with pytest.raises(ValueError, match="window"):
        await cached.prices("AAPL", start=END, end=START)
