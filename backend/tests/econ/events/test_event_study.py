"""Known-answer tests for the market-model event study.

The data is ``make_capm_data`` (asset = alpha + beta * market + eps, validated
in ``test_fixtures.py``) with beta = 1, alpha = 0 and a small residual vol
(0.002), so an injected +2% abnormal return on an event date is the ONLY
abnormal component: the estimated CAR over a tight window must recover ~0.02
and be significant, while uninjected data must stay insignificant (the p-value
is uniform under the null; the committed seed was checked to land clear of the
5% boundary).
"""

import pandas as pd
import pytest

import econometrica.econ.events  # noqa: F401  — registration side-effects
from econometrica.econ.registry import get_registry
from econometrica.econ.types import ResultSet
from tests.econ.fixtures import make_capm_data

N = 600
SEED = 11
INJECT = 0.02


def run_tool(name: str, data: pd.DataFrame, **params: object) -> ResultSet:
    tool = get_registry().get(name)
    return tool.fn(data, tool.params_model(**params))


def base_frame() -> pd.DataFrame:
    return make_capm_data(beta=1.0, alpha=0.0, n=N, seed=SEED, resid_vol=0.002)


def inject(frame: pd.DataFrame, positions: list[int]) -> pd.DataFrame:
    frame = frame.copy()
    for pos in positions:
        frame.iloc[pos, frame.columns.get_loc("asset")] += INJECT
    return frame


def date_at(frame: pd.DataFrame, pos: int) -> str:
    return str(frame.index[pos].strftime("%Y-%m-%d"))


def diagnostic(result: ResultSet, name: str):
    match = next((d for d in result.diagnostics if d.name == name), None)
    assert match is not None, f"missing diagnostic {name!r} in {result.tool}"
    return match


@pytest.fixture(scope="module")
def injected_single() -> tuple[ResultSet, str]:
    frame = base_frame()
    event = date_at(frame, 400)
    result = run_tool(
        "event_study",
        inject(frame, [400]),
        events=[event],
        window_before=2,
        window_after=2,
    )
    return result, event


@pytest.fixture(scope="module")
def injected_pair() -> tuple[ResultSet, list[str]]:
    frame = base_frame()
    events = [date_at(frame, 300), date_at(frame, 450)]
    result = run_tool(
        "event_study",
        inject(frame, [300, 450]),
        events=events,
        window_before=2,
        window_after=2,
    )
    return result, events


def test_event_study_is_registered_in_the_events_family():
    tool = get_registry().get("event_study")
    assert tool.family == "events"
    assert tool.version


def test_injected_abnormal_return_is_recovered_and_significant(
    injected_single: tuple[ResultSet, str],
):
    """+2% injected on the event date: CAR over -2..+2 must be ~0.02 and reject."""
    result, event = injected_single
    table = result.tables["events"]
    assert table.columns == [
        "event",
        "window_start",
        "window_end",
        "estimation_nobs",
        "car",
        "t_stat",
        "p_value",
    ]
    (row,) = table.rows
    assert row[0] == event
    assert row[1] == -2.0 and row[2] == 2.0
    assert row[3] == 250.0
    assert row[4] == pytest.approx(INJECT, abs=0.01)
    diag = diagnostic(result, f"car_{event}")
    assert diag.p_value is not None and diag.p_value < 0.05
    assert diag.passed is True
    assert "H0" in diag.interpretation


def test_car_series_walks_through_the_event_window(injected_single: tuple[ResultSet, str]):
    result, event = injected_single
    series = result.series[f"car_{event}"]
    assert len(series.y) == 5  # -2..+2
    assert series.x[2] == event  # ISO dates, event day in the middle
    final = series.y[-1]
    (row,) = result.tables["events"].rows
    assert final == pytest.approx(row[4])  # path ends at the CAR


def test_single_event_emits_no_caar_outputs(injected_single: tuple[ResultSet, str]):
    result, _event = injected_single
    assert "caar" not in result.series
    assert all(not d.name.startswith("caar") for d in result.diagnostics)


def test_uninjected_event_shows_no_abnormal_return():
    """The null: no injection, CAR insignificant (seed checked, see module doc)."""
    frame = base_frame()
    event = date_at(frame, 400)
    result = run_tool(
        "event_study", frame, events=[event], window_before=2, window_after=2
    )
    diag = diagnostic(result, f"car_{event}")
    assert diag.p_value is not None and diag.p_value > 0.05
    assert diag.passed is False
    (row,) = result.tables["events"].rows
    assert abs(row[4]) < 0.01


def test_two_injected_events_aggregate_to_a_significant_caar(
    injected_pair: tuple[ResultSet, list[str]],
):
    result, _events = injected_pair
    assert result.scalars["n_events"] == 2.0
    assert result.scalars["caar"] == pytest.approx(INJECT, abs=0.01)
    caar = diagnostic(result, "caar")
    assert caar.p_value is not None and caar.p_value < 0.05
    assert caar.passed is True
    # The pure cross-sectional t is also reported; with N=2 it has 1 df and
    # nearly no power, so only its presence and honesty are asserted.
    cross = diagnostic(result, "caar_cross_sectional")
    assert cross.p_value is not None


def test_caar_series_is_indexed_by_relative_day(injected_pair: tuple[ResultSet, list[str]]):
    result, _events = injected_pair
    caar = result.series["caar"]
    assert caar.x == ["-2", "-1", "0", "1", "2"]
    final = caar.y[-1]
    assert final is not None and final == pytest.approx(result.scalars["caar"])


def test_per_event_outputs_exist_alongside_the_aggregate(
    injected_pair: tuple[ResultSet, list[str]],
):
    result, events = injected_pair
    for event in events:
        assert f"car_{event}" in result.series
        assert diagnostic(result, f"car_{event}").p_value is not None
    assert len(result.tables["events"].rows) == 2


def test_event_window_is_truncated_at_the_end_of_the_data():
    """An event 2 days before the sample end keeps a documented shorter window."""
    frame = base_frame()
    event = date_at(frame, N - 3)
    result = run_tool(
        "event_study", frame, events=[event], window_before=2, window_after=10
    )
    (row,) = result.tables["events"].rows
    assert row[1] == -2.0
    assert row[2] == 2.0  # truncated from +10 to the last observation
    assert len(result.series[f"car_{event}"].y) == 5


def test_event_date_not_in_the_index_raises_naming_it():
    frame = base_frame()
    with pytest.raises(ValueError, match="2030-01-01"):
        run_tool("event_study", frame, events=["2030-01-01"])


def test_weekend_event_date_raises_naming_it():
    frame = base_frame()  # business-day index: Saturdays are absent
    with pytest.raises(ValueError, match="2016-01-02"):
        run_tool("event_study", frame, events=["2016-01-02"])


def test_estimation_window_running_off_the_start_raises():
    frame = base_frame()
    with pytest.raises(ValueError, match="estimation"):
        run_tool("event_study", frame, events=[date_at(frame, 100)])


def test_duplicate_event_dates_raise():
    frame = base_frame()
    event = date_at(frame, 400)
    with pytest.raises(ValueError, match="duplicate"):
        run_tool("event_study", frame, events=[event, event])


def test_non_datetime_index_raises():
    frame = base_frame().reset_index(drop=True)
    with pytest.raises(ValueError, match="DatetimeIndex"):
        run_tool("event_study", frame, events=["2016-01-04"])


def test_missing_column_raises_naming_it():
    frame = base_frame().rename(columns={"market": "mkt"})
    with pytest.raises(ValueError, match="market"):
        run_tool("event_study", frame, events=[date_at(frame, 400)])


def test_event_study_results_are_bit_identical_across_runs():
    frame = inject(base_frame(), [400])
    event = date_at(frame, 400)
    a = run_tool("event_study", frame, events=[event], window_before=2, window_after=2)
    b = run_tool("event_study", frame, events=[event], window_before=2, window_after=2)
    assert a.scalars == b.scalars
    assert a.tables["events"].rows == b.tables["events"].rows


def test_event_study_manifest_records_the_tool():
    frame = inject(base_frame(), [400])
    event = date_at(frame, 400)
    result = run_tool("event_study", frame, events=[event])
    assert result.manifest.tool == "event_study"
    assert "statsmodels" in result.manifest.library_versions
