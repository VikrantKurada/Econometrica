"""A stand-in price source, for running the pipeline without market data.

This exists because everything above it — planning, gates, diagnostics,
validation, grounding, tracing — is finished and testable, while the market
data adapters are Phase 6. Refusing to run at all until then would leave the
most interesting part of the system unexercised.

**It is not a simulation of any real asset and must never read as one.** Three
things keep that honest:

* it is never the default, and has to be selected explicitly;
* it names itself, and the Data Steward puts that name in the quality report
  as a `risk`-severity flag, so any run built on it says so;
* it generates a driftless geometric random walk and nothing more — no
  volatility clustering, no fat tails, no regimes. A study of the properties
  of this data would find exactly what was put in.

The random walk is deliberate rather than lazy: the Phase 4 gate asks whether
an asset follows one, and a generator that did something else would make the
gate prove things about the generator instead of about the pipeline.
"""

import hashlib
from datetime import date

import numpy as np
import pandas as pd


class SyntheticPriceSource:
    """A reproducible geometric random walk per ticker."""

    #: Read by the Data Steward and reported. Anything whose label contains
    #: "synthetic" is flagged to the user.
    label = "synthetic (generated, not market data)"

    def __init__(self, *, seed: int = 0, start_price: float = 100.0, vol: float = 0.02) -> None:
        self.seed = seed
        self.start_price = start_price
        self.vol = vol

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        if end <= start:
            raise ValueError(f"the requested window {start}..{end} runs backwards or is empty")

        index = pd.date_range(start, end, freq="D")
        rng = np.random.default_rng(self._seed_for(ticker))
        # Log steps then exponentiate: the level cannot go non-positive, which
        # matters because half the registry takes logs of it.
        steps = rng.normal(loc=0.0, scale=self.vol, size=len(index))
        path = self.start_price * np.exp(np.cumsum(steps))
        return pd.Series(path, index=index, name=ticker)

    def _seed_for(self, ticker: str) -> int:
        """Derive the seed from the ticker, so each asset has its own path.

        Hashed rather than `hash()`: Python's string hashing is salted per
        process, so the same ticker would give a different series on every
        restart and no manifest would ever reproduce.
        """
        digest = hashlib.sha256(f"{self.seed}:{ticker}".encode()).digest()
        return int.from_bytes(digest[:8], "big")
