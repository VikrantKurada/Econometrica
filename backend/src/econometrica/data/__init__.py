"""Where price history comes from.

Phase 6 fills this package with the real adapters — yfinance, Stooq, FRED, Ken
French — behind the `PriceSource` protocol the Data Steward already speaks.
Until then it holds one deliberately synthetic source, so the pipeline can be
run and demonstrated without pretending to have market data.
"""
