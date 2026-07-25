"""Market-model event study (Brown-Warner).

Window arithmetic is in TRADING-DAY POSITIONS of the cleaned frame (rows with
a NaN in either column are dropped first): for an event at position ``pos``,
the estimation window is the ``estimation_window`` observations ending
``gap`` trading days before the event (positions ``pos - gap -
estimation_window .. pos - gap - 1``) and the event window runs from ``pos -
window_before`` to ``pos + window_after``. With ``gap >= window_before`` (the
defaults) the two windows are disjoint; a smaller gap lets early event-window
days leak into the estimation sample and is the caller's responsibility.

Inference is the standard Brown-Warner construction: the abnormal-return
variance is the estimation-window residual variance of the market model, so
``Var(CAR) = L * sigma2_est`` over an ``L``-day window and the per-event
t-statistic has ``estimation_window - 2`` degrees of freedom. Across events
the primary CAAR test aggregates those independent per-event variances
(``Var(CAAR) = sum Var(CAR_i) / N^2``, standard-normal reference); the pure
cross-sectional t (``N - 1`` df) is also reported but has almost no power for
small N — both interpretations say which assumptions they lean on.

Edge behaviour, chosen and documented: an event date missing from the index
raises (naming the date); an estimation window running off the START of the
data raises; an event window running past the END of the data is TRUNCATED at
the last observation, visible in the events table's ``window_end`` column.
"""

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field
from scipy import stats

from econometrica.econ._common import (
    build_manifest,
    coerce_params,
    iso_index,
    require_columns,
)
from econometrica.econ.registry import get_registry
from econometrica.econ.types import Diagnostic, ResultSet, Series, Table

_VERSION = "1.0.0"
_LIBRARIES = ("numpy", "pandas", "scipy", "statsmodels")


class EventStudyParams(BaseModel):
    """Column bindings, event dates and window geometry for the event study."""

    asset: str = Field(default="asset", description="Column of per-period asset returns.")
    market: str = Field(default="market", description="Column of per-period market returns.")
    events: list[str] = Field(
        min_length=1,
        description="Event dates as ISO strings (e.g. '2020-03-16'); each must"
        " be a trading day present in the data's date index.",
    )
    estimation_window: int = Field(
        default=250,
        ge=30,
        description="Trading days used to fit the market model, ending gap"
        " days before each event.",
    )
    gap: int = Field(
        default=10,
        ge=0,
        description="Trading days between the end of the estimation window and"
        " the event date; keep gap >= window_before so the windows stay disjoint.",
    )
    window_before: int = Field(
        default=10, ge=0, description="Event-window days before the event date."
    )
    window_after: int = Field(
        default=10, ge=0, description="Event-window days after the event date."
    )


class _EventResult(BaseModel):
    """Per-event intermediates carried to the aggregation step."""

    label: str
    rel_days: list[int]
    abnormal: list[float]
    car: float
    var_car: float
    t_stat: float
    p_value: float


def _locate(frame: pd.DataFrame, raw: str, *, tool: str) -> tuple[str, int]:
    try:
        ts = pd.Timestamp(raw)
    except ValueError as exc:
        raise ValueError(f"{tool}: event date {raw!r} is not a parseable date") from exc
    label = str(ts.strftime("%Y-%m-%d"))
    index = frame.index
    first = str(index[0].date())
    last = str(index[-1].date())
    try:
        pos = index.get_loc(ts)
    except KeyError:
        raise ValueError(
            f"{tool}: event date {label} is not in the data's date index"
            f" ({first}..{last}, trading days only); supply a trading day"
            " present in the index"
        ) from None
    if not isinstance(pos, int | np.integer):
        raise ValueError(f"{tool}: the date index has duplicate entries at {label}")
    return label, int(pos)


@get_registry().register(
    name="event_study",
    version=_VERSION,
    family="events",
    summary="Market-model event study: per event, abnormal returns and the CAR"
    " over the event window with a Brown-Warner t-test (estimation-window"
    " residual variance); across events, the CAAR with an aggregated"
    " Brown-Warner test and a cross-sectional t. Event windows past the end of"
    " the data are truncated (see the events table); missing dates and"
    " insufficient history raise.",
    params_model=EventStudyParams,
    preconditions=(
        "asset and market columns hold per-period returns on a DatetimeIndex of trading days",
        "each event needs estimation_window + gap trading days of history before it",
    ),
)
def event_study(data: pd.DataFrame, params: BaseModel) -> ResultSet:
    import statsmodels.api as sm

    p = coerce_params(params, EventStudyParams)
    require_columns(data, [p.asset, p.market], tool="event_study")
    if not isinstance(data.index, pd.DatetimeIndex):
        raise ValueError(
            "event_study: the data must carry a DatetimeIndex of trading days;"
            f" got {type(data.index).__name__}"
        )
    frame = data[[p.asset, p.market]].dropna().astype(float).sort_index()
    n = len(frame)
    asset = frame[p.asset].to_numpy()
    market = frame[p.market].to_numpy()

    seen: set[str] = set()
    per_event: list[_EventResult] = []
    series: dict[str, Series] = {}
    rows: list[list[object]] = []
    diagnostics: list[Diagnostic] = []

    for raw in p.events:
        label, pos = _locate(frame, raw, tool="event_study")
        if label in seen:
            raise ValueError(f"event_study: duplicate event date {label}")
        seen.add(label)

        est_end = pos - p.gap  # exclusive
        est_start = est_end - p.estimation_window
        if est_start < 0:
            raise ValueError(
                f"event_study: the estimation window for event {label} needs"
                f" {p.estimation_window} observations ending {p.gap} trading"
                f" days before the event, but only {max(est_end, 0)} are"
                " available; supply more history or shorten estimation_window"
            )
        win_start = pos - p.window_before
        if win_start < 0:
            raise ValueError(
                f"event_study: the event window for event {label} starts"
                f" {p.window_before} days before the event and runs off the"
                " start of the data; supply more history or shorten window_before"
            )
        win_end = min(pos + p.window_after, n - 1)  # truncated at the sample end

        design = sm.add_constant(market[est_start:est_end])
        fit = sm.OLS(asset[est_start:est_end], design).fit()
        alpha_hat = float(fit.params[0])
        beta_hat = float(fit.params[1])
        sigma2 = float(fit.mse_resid)
        df_est = p.estimation_window - 2

        window = slice(win_start, win_end + 1)
        abnormal = asset[window] - (alpha_hat + beta_hat * market[window])
        car_path = np.cumsum(abnormal)
        car = float(car_path[-1])
        length = win_end - win_start + 1
        var_car = float(length * sigma2)
        t_stat = car / float(np.sqrt(var_car))
        p_value = 2.0 * float(stats.t.sf(abs(t_stat), df_est))
        rel_days = list(range(win_start - pos, win_end - pos + 1))

        per_event.append(
            _EventResult(
                label=label,
                rel_days=rel_days,
                abnormal=[float(a) for a in abnormal],
                car=car,
                var_car=var_car,
                t_stat=t_stat,
                p_value=p_value,
            )
        )
        series[f"car_{label}"] = Series(
            name=f"car_{label}",
            x=iso_index(frame.index[window]),
            y=[float(v) for v in car_path],
        )
        rows.append(
            [
                label,
                float(rel_days[0]),
                float(rel_days[-1]),
                float(p.estimation_window),
                car,
                t_stat,
                p_value,
            ]
        )
        diagnostics.append(
            Diagnostic(
                name=f"car_{label}",
                statistic=t_stat,
                p_value=p_value,
                passed=bool(p_value < 0.05),
                interpretation=f"H0: the cumulative abnormal return over days"
                f" [{rel_days[0]}, {rel_days[-1]}] around {label} is zero."
                " passed means H0 is REJECTED at 5% — a significant abnormal"
                " return was detected (here rejection is the finding, not a"
                " failed assumption). Brown-Warner variance: event-window"
                " length x the estimation-window residual variance,"
                f" t with {df_est} df.",
            )
        )

    scalars: dict[str, float] = {"n_events": float(len(per_event))}

    if len(per_event) >= 2:
        cars = np.array([e.car for e in per_event])
        caar = float(cars.mean())
        scalars["caar"] = caar
        n_events = len(per_event)

        # Primary CAAR test: aggregate the independent per-event Brown-Warner
        # variances. Var(CAAR) = sum Var(CAR_i) / N^2, standard-normal reference.
        var_caar = float(sum(e.var_car for e in per_event)) / n_events**2
        bw_t = caar / float(np.sqrt(var_caar))
        bw_p = 2.0 * float(stats.norm.sf(abs(bw_t)))
        diagnostics.append(
            Diagnostic(
                name="caar",
                statistic=bw_t,
                p_value=bw_p,
                passed=bool(bw_p < 0.05),
                interpretation="H0: the cumulative average abnormal return"
                " across events is zero. passed means H0 is rejected at 5%."
                " Brown-Warner aggregation of the per-event estimation-window"
                " variances, assuming independence across events (clustered"
                " event dates violate it).",
            )
        )

        # Secondary: the pure cross-sectional t. Honest but nearly powerless
        # for small N (N - 1 df) — reported with that caveat, not hidden.
        cs_sd = float(cars.std(ddof=1))
        cs_t = caar / (cs_sd / float(np.sqrt(n_events))) if cs_sd > 0 else 0.0
        cs_p = (
            2.0 * float(stats.t.sf(abs(cs_t), n_events - 1)) if cs_sd > 0 else 1.0
        )
        diagnostics.append(
            Diagnostic(
                name="caar_cross_sectional",
                statistic=cs_t,
                p_value=cs_p,
                passed=bool(cs_p < 0.05),
                interpretation="H0: the average CAR across events is zero,"
                " tested against the CROSS-SECTIONAL spread of the per-event"
                f" CARs (t with {n_events - 1} df). Robust to misspecified"
                " event-window variance but nearly powerless for few events;"
                " prefer the Brown-Warner caar diagnostic for small samples.",
            )
        )

        # CAAR path by relative day, averaging over the events that reach each
        # day (truncated events simply drop out of the tail average).
        all_days = sorted({d for e in per_event for d in e.rel_days})
        mean_ar: list[float] = []
        for day in all_days:
            values = [
                e.abnormal[e.rel_days.index(day)] for e in per_event if day in e.rel_days
            ]
            mean_ar.append(float(np.mean(values)))
        series["caar"] = Series(
            name="caar",
            x=[str(day) for day in all_days],
            y=[float(v) for v in np.cumsum(mean_ar)],
        )

    return ResultSet(
        tool="event_study",
        version=_VERSION,
        params=p.model_dump(),
        diagnostics=diagnostics,
        scalars=scalars,
        tables={
            "events": Table(
                columns=[
                    "event",
                    "window_start",
                    "window_end",
                    "estimation_nobs",
                    "car",
                    "t_stat",
                    "p_value",
                ],
                rows=rows,
            )
        },
        series=series,
        manifest=build_manifest(
            data, p, tool="event_study", version=_VERSION, libraries=_LIBRARIES
        ),
    )
