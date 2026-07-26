"""Turning a published rate series into per-period returns.

A risk-free series is not a price and must never be differenced. It is also not
a return until it has been rescaled and re-based to the frequency of whatever it
is being subtracted from, and getting either step wrong is invisible: an alpha
estimated against a rate that is 100x too large is a plausible-looking number
with no error attached to it.

**Why a table rather than a heuristic.** Scale cannot be inferred from the
values. `DGS3MO` at `5.46` and a decimal rate at `0.0546` are the same rate;
`SP500` at `4742.83` is not a rate at all. Reading the magnitude and guessing is
exactly how a rate becomes an index level, so a series with no declared
convention is refused, and the message names what is known — the fix is one
entry in one table.

**Why compounding rather than division.** `5.46/100/252` and
`(1.0546)**(1/252)-1` differ in the fourth significant digit and both appear in
published work. The compounding form is chosen to match Ken French, whose own
file description defines RF as "the simple daily rate that, over the number of
trading days in the month, compounds to" the monthly bill rate — so a study
mixing our risk-free rate with their factors stays internally consistent.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd

from econometrica.agents.data_steward import DataUnavailableError, PriceSource


@dataclass(frozen=True)
class RateConvention:
    """How a published rate series has to be read."""

    #: Divide the published number by this to get a decimal fraction.
    scale: float
    #: Whether the published rate is annualised, and so has to be compounded
    #: down to the frequency it will be subtracted at.
    annualised: bool
    description: str


#: Every FRED series here is published in **percent per annum**. Adding one is a
#: single line; guessing at an unlisted one is refused.
CONVENTIONS: dict[str, RateConvention] = {
    series_id: RateConvention(scale=100.0, annualised=True, description=description)
    for series_id, description in {
        "DGS1MO": "1-month Treasury constant maturity yield",
        "DGS3MO": "3-month Treasury constant maturity yield",
        "DGS6MO": "6-month Treasury constant maturity yield",
        "DGS1": "1-year Treasury constant maturity yield",
        "DGS2": "2-year Treasury constant maturity yield",
        "DGS5": "5-year Treasury constant maturity yield",
        "DGS10": "10-year Treasury constant maturity yield",
        "DGS30": "30-year Treasury constant maturity yield",
        "DTB4WK": "4-week Treasury bill, secondary market rate",
        "DTB3": "3-month Treasury bill, secondary market rate",
        "DTB6": "6-month Treasury bill, secondary market rate",
        "DTB1YR": "1-year Treasury bill, secondary market rate",
        "TB3MS": "3-month Treasury bill, monthly average",
        "DFF": "effective federal funds rate, daily",
        "FEDFUNDS": "effective federal funds rate, monthly",
        "EFFR": "effective federal funds rate",
        "SOFR": "secured overnight financing rate",
    }.items()
}


def to_period_rate(
    published: pd.Series, *, series_id: str, periods_per_year: int
) -> pd.Series:
    """A published rate series as a per-period decimal return."""
    convention = CONVENTIONS.get(series_id)
    if convention is None:
        raise DataUnavailableError(
            f"no rate convention is declared for {series_id!r}, so it cannot be read"
            f" as a risk-free rate without guessing at its scale. Known series:"
            f" {', '.join(sorted(CONVENTIONS))}"
        )

    decimal = published.astype(float) / convention.scale
    if not convention.annualised:
        return decimal

    # (1 + r)^(1/n) - 1 rather than r/n. Defined for negative rates too, which
    # matters: policy rates have been below zero, and a conversion that clamped
    # would misstate a decade of European work.
    converted = (1.0 + decimal) ** (1.0 / periods_per_year) - 1.0
    converted.name = published.name
    return converted


async def resolve_rate(
    source: PriceSource,
    series_id: str,
    *,
    start: date,
    end: date,
    periods_per_year: int,
) -> pd.Series:
    """Fetch a rate series through any source and convert it.

    The source is an ordinary `PriceSource`, so the on-disk cache applies
    unchanged — a rate needs no protocol of its own, only a convention.
    """
    published = await source.prices(series_id, start=start, end=end)
    return to_period_rate(
        published, series_id=series_id, periods_per_year=periods_per_year
    )
