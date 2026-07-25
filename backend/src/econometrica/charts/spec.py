"""The chart spec union.

Every constraint here exists because a chart can mislead in ways a table
cannot, and because the thing emitting these is a language model. Three are
worth stating up front, since they look like style rules and are not:

**No spec can express a second y-axis.** Two measures on two scales share a
crossing point that is an artifact of the scalings, and readers reliably infer
causation from it. Rather than discourage that, the union has no field for it
anywhere, and a test asserts the absence across every member so a type added
later cannot reintroduce it. Two measures become :class:`PanelsChart`.

**The series caps are measured, not chosen.** The palette this renders with
was validated against the project's own chart surfaces: eight slots clear the
adjacent-pair colour-blindness floors, but only the first three clear them
when every pair is compared at once. Scatter compares every pair at once, so
it takes the lower cap.

**A heatmap's scale must match its data's polarity.** Correlations run through
a meaningful zero and need a diverging scale with a neutral midpoint; a
one-hue ramp over them hides the sign, which is the entire reading.
"""

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from econometrica.econ.types import ResultSet

#: Categorical palette slots available. Eight clear the adjacent-pair gates.
MAX_SERIES = 8

#: Forms that compare every pair of series at once rather than side by side.
#: Only the first three palette slots clear the all-pairs floors; past three,
#: facet or fold to "Other".
SCATTER_SERIES_CAP = 3


class SeriesRef(BaseModel):
    """A named series in a `ResultSet`, and how to label it on the chart."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    label: str = ""

    @model_validator(mode="after")
    def label_defaults_to_the_key(self) -> Self:
        if not self.label:
            # Assigned rather than left empty so a renderer never has to guess,
            # and so the legend text is decided here rather than in the client.
            object.__setattr__(self, "label", self.key)
        return self


class ChartBase(BaseModel):
    """Fields every chart carries.

    ``extra="forbid"`` for the reason `PlanStep` rejects unknown parameters: a
    model that invents ``"smoothing": true`` and has it silently dropped
    leaves a reader believing the line was smoothed.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    subtitle: str = ""
    caption: str = ""
    #: The plan step whose ResultSet this draws from, for citation and re-run.
    step_id: str = ""
    x_label: str = ""
    y_label: str = ""


class LineChart(ChartBase):
    """A measure over time or over an ordered index."""

    type: Literal["line"] = "line"
    series: list[SeriesRef] = Field(min_length=1, max_length=MAX_SERIES)


class BandChart(ChartBase):
    """An estimate with a confidence ribbon — rolling beta, CAAR."""

    type: Literal["band"] = "band"
    center: SeriesRef
    lower: SeriesRef
    upper: SeriesRef


class StemChart(ChartBase):
    """Discrete lags against a symmetric significance band — ACF, PACF."""

    type: Literal["stem"] = "stem"
    series: SeriesRef
    upper: SeriesRef
    lower: SeriesRef


class Panel(BaseModel):
    """One row of a stacked panel chart."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    series: list[SeriesRef] = Field(min_length=1, max_length=MAX_SERIES)
    y_label: str = ""


class PanelsChart(ChartBase):
    """Several measures stacked, sharing one x-axis and one crosshair.

    This is what a two-scale overlay becomes. Also the IRF grid and any small
    multiple. Colour is assigned per panel, so the adjacent-pair rule governs
    rather than the all-pairs cap.
    """

    type: Literal["panels"] = "panels"
    #: Two or more: a single panel is a line chart wearing a costume.
    panels: list[Panel] = Field(min_length=2)
    shared_x: bool = True


class ScatterChart(ChartBase):
    """Points, optionally with a fitted line — the security market line."""

    type: Literal["scatter"] = "scatter"
    x: SeriesRef
    y: SeriesRef
    #: Grouping series, each its own colour. Capped below rather than with
    #: `max_length`, whose "List should have at most 3 items" says nothing a
    #: model can act on — the validator names the remedy instead.
    groups: list[SeriesRef] = Field(default_factory=list)
    fit: bool = True

    @model_validator(mode="after")
    def groups_stay_inside_the_all_pairs_cap(self) -> Self:
        if len(self.groups) > SCATTER_SERIES_CAP:
            raise ValueError(
                f"a scatter compares every pair of colours at once, so it carries at"
                f" most {SCATTER_SERIES_CAP} (three) groups; got {len(self.groups)}."
                " Facet into panels, or fold the tail into 'Other'"
            )
        return self


class BarChart(ChartBase):
    """Magnitudes across categories — variance ratios by horizon."""

    type: Literal["bar"] = "bar"
    series: list[SeriesRef] = Field(min_length=1, max_length=MAX_SERIES)
    horizontal: bool = False


class ForestChart(ChartBase):
    """Coefficients with their confidence intervals, read against zero."""

    type: Literal["forest"] = "forest"
    #: Estimate names from `ResultSet.estimates`.
    estimates: list[str] = Field(min_length=1)


class HeatmapChart(ChartBase):
    """A matrix of values — correlations, cointegration p-values."""

    type: Literal["heatmap"] = "heatmap"
    table: str = Field(min_length=1)
    scale: Literal["sequential", "diverging"]
    #: Value range. Used to check the scale suits the data's polarity.
    domain: tuple[float, float] | None = None

    @model_validator(mode="after")
    def the_scale_must_match_the_polarity(self) -> Self:
        if self.domain is None:
            return self
        low, high = self.domain
        if low < 0.0 < high and self.scale != "diverging":
            raise ValueError(
                f"a domain spanning zero ({low} to {high}) needs a diverging scale"
                " with a neutral midpoint; a one-hue sequential ramp over it hides"
                " the sign, which is the reading"
            )
        return self


class QqChart(ChartBase):
    """Sample quantiles against a reference distribution."""

    type: Literal["qq"] = "qq"
    series: SeriesRef
    reference: Literal["normal", "t"] = "normal"


class HistogramChart(ChartBase):
    type: Literal["histogram"] = "histogram"
    series: SeriesRef
    bins: int = Field(default=40, ge=5, le=200)


class AreaStackChart(ChartBase):
    """Shares summing to a whole — forecast error variance decomposition."""

    type: Literal["area_stack"] = "area_stack"
    series: list[SeriesRef] = Field(min_length=1, max_length=MAX_SERIES)


class UnderwaterChart(ChartBase):
    """Drawdown filled down from zero."""

    type: Literal["underwater"] = "underwater"
    series: SeriesRef


class StatTileChart(ChartBase):
    """One number. Sometimes the honest answer is not a chart at all."""

    type: Literal["stat_tile"] = "stat_tile"
    scalar: str = Field(min_length=1)
    unit: str = ""
    precision: int = Field(default=4, ge=0, le=10)


class TableChart(ChartBase):
    """The table view. Every chart needs one available; sometimes it is the chart."""

    type: Literal["table"] = "table"
    table: str = Field(min_length=1)


ChartSpec = Annotated[
    LineChart
    | BandChart
    | StemChart
    | PanelsChart
    | ScatterChart
    | BarChart
    | ForestChart
    | HeatmapChart
    | QqChart
    | HistogramChart
    | AreaStackChart
    | UnderwaterChart
    | StatTileChart
    | TableChart,
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[Any] = TypeAdapter(ChartSpec)

#: Every member, keyed by its discriminator. Iterated by tests and by the
#: renderer's exhaustiveness check.
CHART_TYPES: dict[str, type[ChartBase]] = {
    model.model_fields["type"].default: model
    for model in (
        LineChart, BandChart, StemChart, PanelsChart, ScatterChart, BarChart,
        ForestChart, HeatmapChart, QqChart, HistogramChart, AreaStackChart,
        UnderwaterChart, StatTileChart, TableChart,
    )
}


def parse_chart_spec(payload: dict[str, Any]) -> Any:
    """Validate one spec, raising with the known types named on an unknown one."""
    return _ADAPTER.validate_python(payload)


def unresolved_references(chart: Any, result: ResultSet) -> list[str]:
    """Names the chart uses that the result does not carry.

    A chart of data that does not exist is an ungrounded number with a line
    drawn through it, so this runs before a spec is stored — but it returns
    the problems rather than raising, because the caller retries the model
    with them and needs all of them at once.
    """
    missing: list[str] = []

    def series(*refs: SeriesRef) -> None:
        for ref in refs:
            if ref.key not in result.series:
                missing.append(f"series '{ref.key}'")

    match chart:
        case LineChart() | BarChart() | AreaStackChart():
            series(*chart.series)
        case BandChart():
            series(chart.center, chart.lower, chart.upper)
        case StemChart():
            series(chart.series, chart.upper, chart.lower)
        case PanelsChart():
            for panel in chart.panels:
                series(*panel.series)
        case ScatterChart():
            series(chart.x, chart.y, *chart.groups)
        case QqChart() | HistogramChart() | UnderwaterChart():
            series(chart.series)
        case ForestChart():
            known = {estimate.name for estimate in result.estimates}
            missing.extend(f"estimate '{n}'" for n in chart.estimates if n not in known)
        case HeatmapChart() | TableChart():
            if chart.table not in result.tables:
                missing.append(f"table '{chart.table}'")
        case StatTileChart():
            if chart.scalar not in result.scalars:
                missing.append(f"scalar '{chart.scalar}'")

    return missing
