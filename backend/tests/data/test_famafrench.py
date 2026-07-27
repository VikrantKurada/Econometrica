"""Ken French factors — the source that makes `ff3`, `ff5` and `carhart4` run.

Those three tools have been in the catalogue every Planner reads since Phase 2,
with `factors` defaulting to `["mkt_rf","smb","hml"]`, and nothing could ever
supply a factor column — so `require_columns` raised and the step landed
`failed`. Three of thirty-seven tools unreachable.

Two properties of the published data do the damage if missed, and both have
their own test here: **the values are percent** (`Mkt-RF` of `-0.70` means
-0.70%, and forgetting it rescales every loading by 100), and **the index is a
pandas Period**, not a timestamp, so it joins to nothing.
"""

import threading
from datetime import date

import pandas as pd
import pytest

from econometrica.data.base import DataUnavailableError
from econometrica.data.famafrench import FACTOR_SETS, FamaFrenchFactorSource

START, END = date(2024, 1, 1), date(2024, 3, 31)


class FakeReader:
    """Stands in for `pandas_datareader.famafrench.FamaFrenchReader(...).read()`.

    Returns a dict keyed by table number plus DESCR, which is the shape the real
    reader hands back — monthly files carry an *annual* table at key 1 as well.
    """

    def __init__(self, tables: dict[str, dict[int, pd.DataFrame]]) -> None:
        self.tables = tables
        self.asked: list[str] = []
        self.thread_ids: list[int] = []

    def __call__(self, name: str, *, start: date, end: date) -> dict[object, object]:
        self.asked.append(name)
        self.thread_ids.append(threading.get_ident())
        if name not in self.tables:
            raise ValueError(f"{name} is not a known dataset")
        return {**self.tables[name], "DESCR": f"description of {name}"}


def daily(columns: dict[str, list[float]], periods: int = 3) -> pd.DataFrame:
    index = pd.period_range("2024-01-02", periods=periods, freq="D")
    return pd.DataFrame(columns, index=index)


def monthly(columns: dict[str, list[float]], periods: int = 3) -> pd.DataFrame:
    index = pd.period_range("2024-01", periods=periods, freq="M")
    return pd.DataFrame(columns, index=index)


#: The published values, in percent, exactly as the live probe returned them.
FF3_DAILY = {"Mkt-RF": [-0.70, -1.01, -0.33], "SMB": [-0.55, -1.94, 0.21],
             "HML": [0.78, -0.11, 0.09], "RF": [0.02, 0.02, 0.02]}
FF3_MONTHLY = {"Mkt-RF": [6.61, 1.0, 2.0], "SMB": [5.01, 0.5, 0.5],
               "HML": [-3.94, 0.1, 0.2], "RF": [0.35, 0.35, 0.35]}
FF5_MONTHLY = {**FF3_MONTHLY, "RMW": [-2.44, 0.3, 0.4], "CMA": [-4.43, 0.2, 0.1]}
MOM_MONTHLY = {"Mom": [-16.21, 1.5, 2.5]}


def reader() -> FakeReader:
    return FakeReader(
        {
            "F-F_Research_Data_Factors_daily": {0: daily(FF3_DAILY)},
            # Monthly research files carry the annual table at key 1. Reading
            # that one instead would silently give three annual observations.
            "F-F_Research_Data_Factors": {
                0: monthly(FF3_MONTHLY),
                1: pd.DataFrame(
                    {"Mkt-RF": [21.0]},
                    index=pd.period_range("2024", periods=1, freq="Y"),
                ),
            },
            "F-F_Research_Data_5_Factors_2x3": {0: monthly(FF5_MONTHLY)},
            "F-F_Momentum_Factor": {0: monthly(MOM_MONTHLY)},
        }
    )


async def factors(set_name: str = "ff3", frequency: str = "D", source=None) -> pd.DataFrame:
    source = source or FamaFrenchFactorSource(reader=reader())
    return await source.factors(set_name, start=START, end=END, frequency=frequency)


# --- the two things that silently corrupt results ----------------------------


async def test_percent_becomes_decimal():
    """`Mkt-RF` of -0.70 means -0.70%, so -0.0070. Miss it and every loading is
    off by a factor of a hundred while still looking like a plausible number."""
    frame = await factors("ff3", "D")

    assert frame["mkt_rf"].iloc[0] == pytest.approx(-0.0070)
    assert frame["smb"].iloc[0] == pytest.approx(-0.0055)
    assert frame["hml"].iloc[0] == pytest.approx(0.0078)


async def test_the_period_index_becomes_a_datetime_index():
    """Published as `period[D]`, which joins to nothing on a price calendar."""
    frame = await factors("ff3", "D")

    assert isinstance(frame.index, pd.DatetimeIndex)
    assert frame.index[0] == pd.Timestamp("2024-01-02")


async def test_a_monthly_set_is_labelled_at_period_end():
    """The Data Steward resamples prices with `ME`, so factors have to land on
    the same month-end labels or the join drops everything."""
    frame = await factors("ff3", "M")

    assert list(frame.index[:2]) == [pd.Timestamp("2024-01-31"), pd.Timestamp("2024-02-29")]


async def test_the_annual_table_is_never_read_in_place_of_the_monthly_one():
    """Monthly research files return {0: monthly, 1: annual}. Taking key 1 would
    give a handful of annual observations that look like a short sample."""
    frame = await factors("ff3", "M")

    assert len(frame) == 3
    assert frame["mkt_rf"].iloc[0] == pytest.approx(0.0661)


# --- naming ------------------------------------------------------------------


async def test_columns_arrive_as_the_tool_parameter_defaults():
    """`Ff3Params.factors` defaults to ["mkt_rf","smb","hml"], so a plan naming
    `ff3` with no params must find exactly those columns — the model should not
    have to know Ken French's spelling."""
    frame = await factors("ff3", "D")

    assert {"mkt_rf", "smb", "hml"} <= set(frame.columns)
    assert "Mkt-RF" not in frame.columns


async def test_the_risk_free_column_comes_with_the_factors():
    """Ken French factors are excess returns against *their* RF, so a factor
    study needs no separate rate source."""
    frame = await factors("ff3", "D")

    assert "risk_free" in frame.columns
    assert frame["risk_free"].iloc[0] == pytest.approx(0.0002)


async def test_ff5_supplies_all_five_factors():
    frame = await factors("ff5", "M")

    assert {"mkt_rf", "smb", "hml", "rmw", "cma"} <= set(frame.columns)
    assert frame["rmw"].iloc[0] == pytest.approx(-0.0244)


async def test_carhart4_joins_momentum_from_its_own_file():
    """Momentum is published separately, so this set is two datasets aligned."""
    source = FamaFrenchFactorSource(reader=(r := reader()))

    frame = await factors("carhart4", "M", source=source)

    assert {"mkt_rf", "smb", "hml", "mom"} <= set(frame.columns)
    assert frame["mom"].iloc[0] == pytest.approx(-0.1621)
    assert "F-F_Momentum_Factor" in r.asked
    assert "F-F_Research_Data_Factors" in r.asked


async def test_carhart4_takes_smb_from_the_three_factor_file():
    """The 5-factor file constructs SMB differently — 4.40 against 5.01 for the
    same month. Carhart is FF3 plus momentum, so it must use the FF3 file."""
    frame = await factors("carhart4", "M")

    assert frame["smb"].iloc[0] == pytest.approx(0.0501)


# --- what cannot be built ----------------------------------------------------


async def test_daily_carhart4_is_refused_with_the_reason():
    """`F-F_Momentum_Factor_daily` cannot be parsed by pandas-datareader 0.11.1:
    famafrench.py:118 compares a string index against an int and raises
    TypeError at every date range. Better to say so than to fail inside a
    library the user did not call."""
    with pytest.raises(DataUnavailableError, match="carhart4"):
        await factors("carhart4", "D")


async def test_an_unknown_factor_set_names_the_known_ones():
    with pytest.raises(DataUnavailableError, match="ff3"):
        await factors("ff6", "M")


async def test_a_dataset_that_cannot_be_read_names_the_set():
    source = FamaFrenchFactorSource(reader=FakeReader({}))

    with pytest.raises(DataUnavailableError, match="ff3"):
        await factors("ff3", "D", source=source)


async def test_a_backwards_window_is_refused_before_any_fetch():
    r = reader()
    source = FamaFrenchFactorSource(reader=r)

    with pytest.raises(ValueError, match="window"):
        await source.factors("ff3", start=END, end=START, frequency="D")

    assert r.asked == []


# --- mechanics ---------------------------------------------------------------


async def test_the_blocking_read_runs_off_the_event_loop():
    r = reader()

    await factors("ff3", "D", source=FamaFrenchFactorSource(reader=r))

    assert r.thread_ids[0] != threading.get_ident()


async def test_a_dataset_is_fetched_once_per_resolve():
    """carhart4 reads the FF3 file for its factors and the momentum file for
    `mom`; neither should be fetched twice."""
    r = reader()

    await factors("carhart4", "M", source=FamaFrenchFactorSource(reader=r))

    assert sorted(r.asked) == ["F-F_Momentum_Factor", "F-F_Research_Data_Factors"]


def test_every_declared_set_matches_a_tool_parameter_default():
    """The registry is the authority on what a factor model expects. If a set
    here drifted from its tool's defaults, a plan naming the tool with no
    params would fail on a column it never chose."""
    from econometrica.econ import load_tools
    from econometrica.econ.registry import get_registry

    load_tools()
    for name, factor_set in FACTOR_SETS.items():
        declared = get_registry().get(name).params_model.model_fields["factors"].default
        assert set(factor_set.factors) == set(declared), name


# --- live -------------------------------------------------------------------


def _reachable() -> bool:
    import httpx

    try:
        httpx.get("https://mba.tuck.dartmouth.edu/", timeout=8.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_the_daily_three_factor_file_matches_the_probe():
    """`Mkt-RF` for 2024-01-02 is published as -0.70. This is the assertion that
    proves the percent conversion against the real file rather than against a
    frame this test built."""
    if not _reachable():
        pytest.skip("ken french data library is not reachable")

    frame = await FamaFrenchFactorSource().factors(
        "ff3", start=date(2024, 1, 2), end=date(2024, 1, 5), frequency="D"
    )

    assert frame["mkt_rf"].loc[pd.Timestamp("2024-01-02")] == pytest.approx(-0.0070)
    assert frame["risk_free"].loc[pd.Timestamp("2024-01-02")] == pytest.approx(0.0002)


@pytest.mark.live
@pytest.mark.parametrize("set_name", ["ff3", "ff5", "carhart4"])
async def test_live_every_factor_set_loads_monthly(set_name):
    if not _reachable():
        pytest.skip("ken french data library is not reachable")

    frame = await FamaFrenchFactorSource().factors(
        set_name, start=date(2020, 1, 1), end=date(2023, 12, 31), frequency="M"
    )

    expected = set(FACTOR_SETS[set_name].factors)
    assert expected <= set(frame.columns)
    assert len(frame) >= 40
    # Monthly factor returns live in single-digit percent. If any of these came
    # back above 1.0 the percent conversion was skipped for that column.
    assert frame[sorted(expected)].abs().max().max() < 1.0
