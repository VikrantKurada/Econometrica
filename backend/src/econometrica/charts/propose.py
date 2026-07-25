"""Which charts a result implies.

Deterministic, and deliberately so. A `ResultSet`'s shape settles what can be
drawn from it — a GARCH fit has a conditional volatility path and standardized
residuals, an IRF has one series per impulse-response pair — and asking a model
to rediscover that per run buys nothing and can get it wrong.

Two invariants hold for everything returned here, both tested:

* **Every proposed chart binds.** `unresolved_references` comes back empty
  against the result it was proposed for. A chart of data that does not exist
  is an ungrounded number with a line through it.
* **Rules are keyed on shape before name.** A tool that starts emitting
  `residuals` gets a QQ plot without this module being edited, which is what
  keeps 37 tools from needing 37 entries.
"""

from collections.abc import Callable
from typing import Any

from econometrica.charts.spec import (
    MAX_SERIES,
    AreaStackChart,
    BandChart,
    BarChart,
    ForestChart,
    LineChart,
    Panel,
    PanelsChart,
    QqChart,
    SeriesRef,
    StatTileChart,
    StemChart,
    TableChart,
    UnderwaterChart,
)
from econometrica.econ.types import ResultSet

#: Tools whose fit produces a volatility path.
_GARCH_FAMILY = ("garch", "egarch", "gjr_garch")


def propose_charts(result: ResultSet) -> list[Any]:
    """Every chart this result supports, most informative first."""
    charts: list[Any] = []
    for rule in _RULES:
        charts.extend(rule(result))

    # A result nothing else could draw still deserves to be readable.
    if not charts:
        charts.extend(_fallback(result))
    return charts


# --- rules ------------------------------------------------------------------


def _volatility(result: ResultSet) -> list[Any]:
    """The design's "conditional volatility overlay", as two panels.

    Not one plot with two y-scales: the crossing point of two differently
    scaled series is an artifact of the scaling. Both panels come from the fit
    and share its time axis, so they are directly comparable in x.
    """
    if result.tool not in _GARCH_FAMILY or "conditional_volatility" not in result.series:
        return []

    volatility = SeriesRef(key="conditional_volatility")
    panels = [Panel(title="Conditional volatility", series=[volatility])]
    if "standardized_residuals" in result.series:
        panels.append(
            Panel(title="Standardized residuals", series=[SeriesRef(key="standardized_residuals")])
        )
    if len(panels) < 2:
        # A panels chart needs two; alone, the volatility path is just a line.
        return [LineChart(title="Conditional volatility", series=[volatility])]

    return [
        PanelsChart(
            title=f"{result.tool.upper()} fit",
            subtitle="Fitted volatility and the residuals it leaves behind",
            panels=panels,
            x_label="Observation",
        )
    ]


def _impulse_response(result: ResultSet) -> list[Any]:
    """One panel per impulse-response pair — the IRF grid.

    The pair keys (`a->b`) are the series names the tool emits, so the grid
    follows the system's size without this rule knowing it.
    """
    if result.tool != "irf":
        return []
    pairs = [key for key in result.series if "->" in key]
    if not pairs:
        return []

    return [
        PanelsChart(
            title="Impulse responses",
            subtitle="Response of each variable to a one-standard-deviation shock",
            panels=[
                Panel(title=_pair_title(pair), series=[SeriesRef(key=pair)]) for pair in pairs
            ],
            x_label="Horizon",
        )
    ]


def _variance_decomposition(result: ResultSet) -> list[Any]:
    if result.tool != "fevd":
        return []
    shares = [key for key in result.series if "->" in key][:MAX_SERIES]
    if not shares:
        return []
    return [
        AreaStackChart(
            title="Forecast error variance decomposition",
            series=[SeriesRef(key=key, label=_pair_title(key)) for key in shares],
            x_label="Horizon",
            y_label="Share of variance",
        )
    ]


def _rolling_band(result: ResultSet) -> list[Any]:
    """An estimate with its confidence ribbon."""
    if not {"beta", "beta_ci_low", "beta_ci_high"} <= set(result.series):
        return []
    return [
        BandChart(
            title="Rolling beta",
            subtitle="Shaded band is the confidence interval",
            center=SeriesRef(key="beta", label="Beta"),
            lower=SeriesRef(key="beta_ci_low"),
            upper=SeriesRef(key="beta_ci_high"),
        )
    ]


def _autocorrelation(result: ResultSet) -> list[Any]:
    """ACF and PACF as two stem charts, each against its own band."""
    charts: list[Any] = []
    for key, title in (("acf", "Autocorrelation"), ("pacf", "Partial autocorrelation")):
        if {key, f"{key}_upper", f"{key}_lower"} <= set(result.series):
            charts.append(
                StemChart(
                    title=title,
                    subtitle="Bars outside the band are significant at the tested level",
                    series=SeriesRef(key=key),
                    upper=SeriesRef(key=f"{key}_upper"),
                    lower=SeriesRef(key=f"{key}_lower"),
                    x_label="Lag",
                )
            )
    return charts


def _variance_ratio(result: ResultSet) -> list[Any]:
    if "vr_by_horizon" not in result.series:
        return []
    return [
        BarChart(
            title="Variance ratio by horizon",
            subtitle="A random walk sits at 1 across every horizon",
            series=[SeriesRef(key="vr_by_horizon", label="Variance ratio")],
            x_label="Horizon",
        )
    ]


def _drawdown(result: ResultSet) -> list[Any]:
    if "drawdown" not in result.series:
        return []
    return [
        UnderwaterChart(
            title="Drawdown",
            subtitle="Depth below the running maximum",
            series=SeriesRef(key="drawdown"),
        )
    ]


def _regimes(result: ResultSet) -> list[Any]:
    """Regime probabilities stack to one, so they stack visually too."""
    keys = sorted(key for key in result.series if key.startswith("regime_"))
    if len(keys) < 2:
        return []
    return [
        AreaStackChart(
            title="Regime probabilities",
            series=[SeriesRef(key=key, label=key.replace("_", " ")) for key in keys[:MAX_SERIES]],
            y_label="Probability",
        )
    ]


def _plain_series(result: ResultSet) -> list[Any]:
    """Series that are simply a measure over time."""
    charts: list[Any] = []
    for key, title in (
        ("realized_vol", "Realized volatility"),
        ("ewma_vol", "EWMA volatility"),
        ("spread", "Cointegrating spread"),
        ("rs_by_window", "Rescaled range by window"),
    ):
        if key in result.series:
            charts.append(LineChart(title=title, series=[SeriesRef(key=key)]))
    return charts


def _coefficients(result: ResultSet) -> list[Any]:
    """A forest plot reads coefficients against zero, which is the question."""
    named = [estimate.name for estimate in result.estimates if estimate.ci_low is not None]
    if len(named) < 2:
        return []
    return [
        ForestChart(
            title="Coefficients",
            subtitle="Intervals crossing zero are not distinguishable from no effect",
            estimates=named,
        )
    ]


def _residual_diagnostics(result: ResultSet) -> list[Any]:
    """Shape-keyed, not name-keyed: any tool emitting residuals gets this."""
    if "residuals" not in result.series:
        return []
    return [
        QqChart(
            title="Residual QQ plot",
            subtitle="Departure from the line is departure from normality",
            series=SeriesRef(key="residuals"),
        )
    ]


def _fallback(result: ResultSet) -> list[Any]:
    """Something readable for a result nothing above could draw."""
    if result.tables:
        name = next(iter(result.tables))
        return [TableChart(title=name.replace("_", " ").capitalize(), table=name)]
    if result.scalars:
        return [
            StatTileChart(title=name.replace("_", " ").capitalize(), scalar=name)
            for name in list(result.scalars)[:4]
        ]
    return []


_RULES: tuple[Callable[[ResultSet], list[Any]], ...] = (
    _volatility,
    _impulse_response,
    _variance_decomposition,
    _rolling_band,
    _autocorrelation,
    _variance_ratio,
    _drawdown,
    _regimes,
    _plain_series,
    _coefficients,
    _residual_diagnostics,
)


def _pair_title(pair: str) -> str:
    impulse, _, response = pair.partition("->")
    return f"{impulse} → {response}"
