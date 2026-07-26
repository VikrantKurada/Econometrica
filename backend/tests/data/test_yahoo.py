"""The yfinance price source.

The unit tests here drive a fake downloader, because what they are checking is
the adapter's beliefs about yfinance's *shape* — which column carries the
number we want, what an unknown ticker looks like, whose thread the blocking
call runs on. The live tests at the bottom check those beliefs against the real
service, which is the only place they can actually be wrong.

Four of them exist because the real service already disagreed with the parent
plan: yfinance is 1.5.2, `auto_adjust=True` is the default and hides
`Adj Close`, a bad ticker returns an empty frame rather than raising, columns
arrive as a MultiIndex even for one ticker, and `end` is exclusive.
"""

import threading
from datetime import date, timedelta

import pandas as pd
import pytest

from econometrica.agents.data_steward import DataUnavailableError
from econometrica.data.yahoo import YahooPriceSource

START, END = date(2024, 1, 2), date(2024, 1, 19)


class FakeDownloader:
    """Stands in for `yfinance.download`, recording how it was called."""

    def __init__(self, frame: pd.DataFrame | None) -> None:
        self.frame = frame
        self.calls: list[dict[str, object]] = []
        self.thread_ids: list[int] = []

    def __call__(self, tickers: str, **kwargs: object) -> pd.DataFrame | None:
        self.calls.append({"tickers": tickers, **kwargs})
        self.thread_ids.append(threading.get_ident())
        return self.frame


def frame(
    *,
    close: list[float] | None = None,
    adj_close: list[float] | None = None,
    index: pd.DatetimeIndex | None = None,
    multi_level: bool = False,
    ticker: str = "AAPL",
    drop_adj_close: bool = False,
) -> pd.DataFrame:
    """A yfinance-shaped frame. Close and Adj Close differ by default, so a
    test cannot pass by reading the wrong one."""
    close = close if close is not None else [124.82, 126.52, 125.01]
    adj_close = adj_close if adj_close is not None else [121.07, 122.72, 121.25]
    index = index if index is not None else pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04"]
    )

    data = {"Close": close, "High": close, "Low": close, "Open": close, "Volume": [1, 2, 3]}
    if not drop_adj_close:
        data = {"Adj Close": adj_close, **data}

    built = pd.DataFrame(data, index=index)
    built.index.name = "Date"
    if multi_level:
        built.columns = pd.MultiIndex.from_product(
            [list(built.columns), [ticker]], names=["Price", "Ticker"]
        )
    return built


async def fetch(
    downloader: FakeDownloader, ticker: str = "AAPL", *, start: date = START, end: date = END
) -> pd.Series:
    source = YahooPriceSource(downloader=downloader)
    return await source.prices(ticker, start=start, end=end)


# --- what the adapter reads --------------------------------------------------


async def test_it_returns_the_dividend_adjusted_close_not_the_raw_close():
    """The two differ by 3.1% on AAPL days before its 2020 split, and a beta
    estimated on one is not the beta estimated on the other."""
    downloader = FakeDownloader(frame())

    series = await fetch(downloader)

    assert list(series) == pytest.approx([121.07, 122.72, 121.25])


async def test_it_asks_for_an_unadjusted_frame_so_the_choice_stays_ours():
    """`auto_adjust=True` is yfinance 1.x's default and removes `Adj Close`
    entirely, leaving no way to tell which policy produced a number."""
    downloader = FakeDownloader(frame())

    await fetch(downloader)

    assert downloader.calls[0]["auto_adjust"] is False


async def test_a_frame_with_no_adjusted_close_is_refused():
    """Silently falling back to `Close` would make the label a lie. Yahoo
    serves `Adj Close` for equities, indices, FX and crypto alike, so its
    absence is an anomaly rather than a case to paper over."""
    downloader = FakeDownloader(frame(drop_adj_close=True))

    with pytest.raises(DataUnavailableError, match="Adj Close"):
        await fetch(downloader)


async def test_the_series_is_named_for_the_ticker_and_indexed_by_date():
    downloader = FakeDownloader(frame())

    series = await fetch(downloader, "AAPL")

    assert series.name == "AAPL"
    assert isinstance(series.index, pd.DatetimeIndex)


async def test_multi_level_columns_are_flattened():
    """`multi_level_index=False` is a request, not a guarantee across versions,
    and the level is present even for a single ticker."""
    downloader = FakeDownloader(frame(multi_level=True))

    series = await fetch(downloader, "AAPL")

    assert list(series) == pytest.approx([121.07, 122.72, 121.25])


async def test_a_timezone_aware_index_is_made_naive():
    """The Data Steward windows against `pd.Timestamp(spec.start)`, which is
    naive; comparing that to an aware index raises."""
    aware = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"], utc=True)
    downloader = FakeDownloader(frame(index=aware))

    series = await fetch(downloader)

    assert pd.DatetimeIndex(series.index).tz is None


async def test_rows_with_no_price_are_dropped():
    downloader = FakeDownloader(frame(adj_close=[121.07, float("nan"), 121.25]))

    series = await fetch(downloader)

    assert len(series) == 2
    assert series.notna().all()


# --- the window -------------------------------------------------------------


async def test_the_requested_end_date_is_included():
    """yfinance's `end` is exclusive: asking for 2024-01-19 returns data to
    2024-01-18, so passing it through would quietly lose a trading day off
    every window."""
    downloader = FakeDownloader(frame())

    await fetch(downloader, start=START, end=END)

    assert downloader.calls[0]["end"] == (END + timedelta(days=1)).isoformat()
    assert downloader.calls[0]["start"] == START.isoformat()


async def test_a_backwards_window_is_refused_before_any_fetch():
    downloader = FakeDownloader(frame())

    with pytest.raises(ValueError, match="window"):
        await fetch(downloader, start=END, end=START)

    assert downloader.calls == []


# --- failure ----------------------------------------------------------------


async def test_an_empty_frame_is_refused_with_the_ticker_named():
    """An unknown ticker does not raise in yfinance 1.5 — it returns a (0, 6)
    frame and logs `possibly delisted`. The Data Steward's contract is that a
    named ticker either resolves or raises with the ticker named."""
    downloader = FakeDownloader(pd.DataFrame())

    with pytest.raises(DataUnavailableError, match="NOTATICKER"):
        await fetch(downloader, "NOTATICKER")


async def test_a_none_result_is_refused():
    """`yf.download` is annotated `Optional[DataFrame]`."""
    downloader = FakeDownloader(None)

    with pytest.raises(DataUnavailableError, match="NOTATICKER"):
        await fetch(downloader, "NOTATICKER")


async def test_a_frame_of_nothing_but_gaps_is_refused():
    downloader = FakeDownloader(frame(adj_close=[float("nan")] * 3))

    with pytest.raises(DataUnavailableError, match="AAPL"):
        await fetch(downloader)


# --- how it is called -------------------------------------------------------


async def test_the_blocking_download_runs_off_the_event_loop():
    """yfinance is synchronous and reaches the network through curl_cffi. Left
    on the loop it would stall every other request for the length of a fetch."""
    downloader = FakeDownloader(frame())

    await fetch(downloader)

    assert downloader.thread_ids[0] != threading.get_ident()


# --- what it says about itself -----------------------------------------------


async def test_the_label_names_the_source_and_the_adjustment_policy():
    """`label` becomes `DataQualityReport.source`, which is what a reader uses
    to reproduce a number. "yfinance" alone does not distinguish the 124.82
    from the 121.07."""
    label = YahooPriceSource().label.lower()

    assert "yfinance" in label
    assert "adjust" in label


async def test_the_label_does_not_claim_to_be_synthetic():
    """The Data Steward raises its `synthetic_data` risk flag on a substring
    match, so a real source whose label contained the word would tell every
    reader its market data was generated."""
    assert "synthetic" not in YahooPriceSource().label.lower()


# --- live -------------------------------------------------------------------
#
# These are the tests that can actually catch a wrong belief. Every assertion
# above is about a frame this file built.


def _yahoo_is_reachable() -> bool:
    import httpx

    try:
        httpx.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=5.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_a_real_ticker_resolves():
    if not _yahoo_is_reachable():
        pytest.skip("yahoo finance is not reachable")

    series = await YahooPriceSource().prices("AAPL", start=START, end=END)

    assert len(series) >= 5
    assert (series > 0).all()
    assert series.notna().all()
    assert series.name == "AAPL"


@pytest.mark.live
async def test_live_the_window_does_not_over_return():
    """Over-returning would trip the Data Steward's `look_ahead` risk flag on
    every real run — so this checks the end-exclusivity handling from the
    outside, not just the argument passed in."""
    if not _yahoo_is_reachable():
        pytest.skip("yahoo finance is not reachable")

    series = await YahooPriceSource().prices("AAPL", start=START, end=END)

    assert series.index.min().date() >= START
    assert series.index.max().date() <= END


@pytest.mark.live
async def test_live_the_adjusted_close_is_what_comes_back_across_a_split():
    """AAPL split 4-for-1 in August 2020. On 2020-08-25 Yahoo's Close is
    124.82 and its Adj Close 121.08 — a 3.1% gap from one vendor on one day
    that nothing in a ResultSet distinguishes. This is why the label names the
    policy, and it is the assertion that keeps the two from being swapped."""
    if not _yahoo_is_reachable():
        pytest.skip("yahoo finance is not reachable")

    series = await YahooPriceSource().prices(
        "AAPL", start=date(2020, 8, 25), end=date(2020, 8, 26)
    )

    assert series.iloc[0] == pytest.approx(121.08, abs=0.5)


@pytest.mark.live
async def test_live_crypto_resolves_on_its_own_calendar():
    """The Phase 4 gate question is about Bitcoin, and crypto trades every day
    — the equity path must not have assumed a five-day week anywhere."""
    if not _yahoo_is_reachable():
        pytest.skip("yahoo finance is not reachable")

    series = await YahooPriceSource().prices(
        "BTC-USD", start=date(2024, 1, 1), end=date(2024, 1, 8)
    )

    assert len(series) == 8


@pytest.mark.live
async def test_live_an_unknown_ticker_is_refused():
    """The belief this proves — empty frame, no exception — is the one the
    parent plan got wrong."""
    if not _yahoo_is_reachable():
        pytest.skip("yahoo finance is not reachable")

    with pytest.raises(DataUnavailableError, match="NOTATICKERXYZ"):
        await YahooPriceSource().prices("NOTATICKERXYZ", start=START, end=END)
