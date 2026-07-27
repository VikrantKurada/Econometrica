"""The source that refuses.

The default, and deliberately so: a workbench whose numbers are supposed to
trace to real data should not silently substitute anything when no adapter is
selected. It is a source rather than a `None` check at the call site so that
every path through the Data Steward is exercised by something, and so the
message reaches the user attached to the ticker that could not be resolved.
"""

from datetime import date

import pandas as pd

from econometrica.data.base import DataUnavailableError


class UnconfiguredPriceSource:
    label = "none configured"

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        raise DataUnavailableError(
            f"no market data source is configured, so {ticker} cannot be resolved."
            " Set ECONOMETRICA_PRICE_SOURCE=yahoo for real prices, or"
            " =synthetic to run the pipeline on generated data that is flagged"
            " as such in every report."
        )
