"""An on-disk cache in front of any `PriceSource`.

**Why not the Timescale hypertable.** The design puts price series in Postgres,
and it will — for *datasets*, which have a user-facing id, a retained blob and
confirmed column roles. A cache entry is a different object: its identity is
``(source, symbol, window)``, its only correctness criterion is that the same
bytes come back, and it must be safe to delete. Putting the two in one table
would make cache eviction a data-loss risk. `storage/` is gitignored and
already holds `keys.enc`, so a directory under it can be thrown away.

**Why entries expire.** A vendor recomputes adjusted closes every time a split
or a dividend happens, so the series for a fixed window is not fixed over time.
An entry served long enough after it was written is a *different series*, and a
re-run that "reproduced" from it would be reporting on the cache rather than on
the data — which would quietly hollow out the one claim this project rests on.
So entries have a maximum age, and past it they are refetched.

That leaves the case where the entry is stale *and* the source is unreachable.
It raises. Serving the stale copy would be indistinguishable, to everything
above, from having fetched it — and there is no channel to say otherwise:
`DataQualityReport.source` is read from `label` before the fetch happens.
"""

import hashlib
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from econometrica.data.base import DataUnavailableError, PriceSource

#: Long enough that a run, its re-run and its exports share one fetch; short
#: enough that yesterday's adjusted closes are not presented as today's.
DEFAULT_MAX_AGE = timedelta(days=1)

#: Everything else in a symbol is replaced. Real tickers carry `^`, `=` and `.`
#: — `^GSPC`, `EURUSD=X`, `BRK-B` — and none of those may reach a path
#: unexamined on Windows.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

_WINDOW = re.compile(r"^(\d{8})_(\d{8})$")


class CachingPriceSource:
    """Serves a wrapped source's prices from disk when it already has them."""

    def __init__(
        self,
        source: PriceSource,
        *,
        namespace: str,
        root: Path,
        max_age: timedelta = DEFAULT_MAX_AGE,
    ) -> None:
        self._source = source
        self._namespace = namespace
        self._root = root
        self._max_age = max_age

    @property
    def label(self) -> str:
        """The wrapped source's own label.

        Whether a fetch hit the disk is not a fact about where the prices came
        from. Reporting it would make two identical analyses read differently.
        """
        return getattr(self._source, "label", "") or type(self._source).__name__

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        entries = self._covering(ticker, start, end)

        fresh = [entry for entry in entries if self._is_fresh(entry)]
        for entry in fresh:
            served = _read(entry, ticker)
            if served is not None:
                return _slice(served, start, end)

        try:
            fetched = await self._source.prices(ticker, start=start, end=end)
        except Exception as exc:
            # A stale entry exists but was deliberately not used, so say so:
            # the user can widen `max_age` if that is the trade they want.
            if entries:
                raise DataUnavailableError(
                    f"{ticker}: {self.label} is unreachable ({exc}), and the cached"
                    f" copy is stale — adjusted prices are recomputed on every split"
                    f" and dividend, so serving it would report the cache rather than"
                    f" the data"
                ) from exc
            if isinstance(exc, DataUnavailableError | ValueError):
                raise
            raise DataUnavailableError(
                f"{ticker}: {self.label} is unreachable ({exc}) and nothing is cached"
                f" for this window"
            ) from exc

        self._write(ticker, start, end, fetched)
        return fetched

    # --- internals ----------------------------------------------------------

    def _directory(self, ticker: str) -> Path:
        # The hash suffix is what makes the path unique: `^GSPC` and `_GSPC`
        # sanitise identically, and one serving the other's prices would be
        # silent and wrong.
        digest = hashlib.sha256(ticker.encode()).hexdigest()[:8]
        symbol = f"{_UNSAFE.sub('_', ticker)}-{digest}"
        return self._root / _UNSAFE.sub("_", self._namespace) / symbol

    def _covering(self, ticker: str, start: date, end: date) -> list[Path]:
        """Cached entries whose requested window contains this one.

        Matched on the window that was *asked for*, not on the data that came
        back: a window opening on a weekend gets data from the Monday, and
        comparing against that would make the request permanently uncacheable.
        """
        directory = self._directory(ticker)
        if not directory.is_dir():
            return []

        found: list[Path] = []
        for path in sorted(directory.glob("*.parquet")):
            window = _WINDOW.match(path.stem)
            if window is None:
                continue
            cached_start = datetime.strptime(window.group(1), "%Y%m%d").date()
            cached_end = datetime.strptime(window.group(2), "%Y%m%d").date()
            if cached_start <= start and end <= cached_end:
                found.append(path)
        return found

    def _is_fresh(self, path: Path) -> bool:
        try:
            written = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            return False
        return datetime.now() - written <= self._max_age

    def _write(self, ticker: str, start: date, end: date, series: pd.Series) -> None:
        directory = self._directory(ticker)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{start:%Y%m%d}_{end:%Y%m%d}.parquet"

        # Written aside and renamed: a reader arriving mid-write would otherwise
        # see a truncated file, and the recovery path for that is a refetch the
        # caller did not ask for.
        staging = path.with_suffix(".parquet.tmp")
        try:
            series.to_frame(name=str(series.name or ticker)).to_parquet(staging)
            os.replace(staging, path)
        except Exception:
            # A cache that can fail a fetch is worse than no cache.
            staging.unlink(missing_ok=True)


def _read(path: Path, ticker: str) -> pd.Series | None:
    """The stored series, or None if the file cannot be read.

    Corruption is not exceptional enough to raise: a half-written or truncated
    entry should cost a refetch, not the request.
    """
    try:
        frame = pd.read_parquet(path)
    except Exception:
        return None
    if frame.empty or len(frame.columns) != 1:
        return None

    series = frame.iloc[:, 0]
    series.index = pd.DatetimeIndex(series.index)
    series.name = ticker
    return series


def _slice(series: pd.Series, start: date, end: date) -> pd.Series:
    index = pd.DatetimeIndex(series.index)
    return series[(index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))]
