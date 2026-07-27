"""The vocabulary every data source speaks.

These two names started in `agents/data_steward.py`, because the steward was
the only thing that consumed them. Five adapters later that had inverted the
layering: `data/` is the lower layer, and every module in it was reaching up
into `agents/` for its core types. Moving them down here is what lets the
steward call into `data/` — resolving a risk-free rate needs
`data.rates.resolve_rate`, and with the protocol still defined above it that
import is a cycle.

`agents/data_steward.py` re-exports both, so every existing import site keeps
working and nothing outside `data/` had to change.
"""

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


class DataUnavailableError(ValueError):
    """The requested data could not be assembled into a usable frame."""


@runtime_checkable
class PriceSource(Protocol):
    """Where a series comes from.

    Injected rather than imported: `data/registry.py` owns the adapters, and the
    Data Steward's own behaviour — alignment, frequency, returns, quality —
    needs no network to be tested.

    Runtime-checkable so the registry's own tests can assert that everything it
    builds satisfies the protocol. That check is structural — it sees that
    `label` and `prices` exist, not that their signatures match — which is
    enough for its purpose and is what mypy covers properly.
    """

    @property
    def label(self) -> str:
        """Names the adapter in the quality report.

        Which source produced a number, and under which adjustment policy, is
        part of reproducing it: Yahoo's split-adjusted and dividend-adjusted
        closes for AAPL days before its 2020 split differ by 3.1%.

        Read-only, so an implementation may satisfy it with a plain class
        attribute — as most do — or delegate, as the cache wrapper does.
        Declaring it settable would rule the second out for no gain; nothing
        writes to it.
        """
        ...

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        """History for one ticker or series id, indexed by date.

        Named for the common case. A source may serve anything shaped like a
        series — FRED yields and index levels both arrive through here — and
        what the numbers *mean* is declared elsewhere: `data/rates.py` for a
        rate, the tool's own parameters for everything else.
        """
        ...
