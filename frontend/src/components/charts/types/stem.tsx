import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  bind,
  LINE_WIDTH,
  MARKER_SIZE,
  mergeLayout,
  perMark,
  wash,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { StemChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

/**
 * Discrete lags against a symmetric significance band — ACF and PACF.
 *
 * The stems are drawn as line segments rather than as bars so they can carry
 * the 2px round cap and an 8px marker at the tip; a bar that thin cannot show
 * a rounded data-end. The band is reference chrome, not a series, so it wears
 * the gridline grey — painting it a categorical hue would make the reader look
 * for the series it belongs to.
 */
export function buildStem(spec: StemChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  const color = theme.series[0];
  const [stems] = bind(result, spec.series);
  const [upper] = bind(result, spec.upper);
  const [lower] = bind(result, spec.lower);

  const data = [];
  if (lower && upper) {
    data.push(
      {
        type: "scatter" as const,
        mode: "lines" as const,
        x: lower.series.x,
        y: lower.series.y,
        line: { width: 0, color: theme.gridline },
        showlegend: false,
        hoverinfo: "skip" as const,
      },
      {
        type: "scatter" as const,
        mode: "lines" as const,
        name: "Significance band",
        x: upper.series.x,
        y: upper.series.y,
        line: { width: 0, color: theme.gridline },
        fill: "tonexty" as const,
        fillcolor: wash(theme.gridline, 0.55),
        showlegend: false,
        hoverinfo: "skip" as const,
      },
    );
  }

  if (stems) {
    // Three points per lag — baseline, tip, break — so one trace draws every
    // stem without Plotly joining them into a single path.
    const x: (string | number | null)[] = [];
    const y: (number | null)[] = [];
    stems.series.x.forEach((lag, i) => {
      x.push(lag, lag, null);
      y.push(0, stems.series.y[i], null);
    });

    data.push(
      {
        type: "scatter" as const,
        mode: "lines" as const,
        x,
        y,
        line: { color, width: LINE_WIDTH },
        showlegend: false,
        hoverinfo: "skip" as const,
      },
      {
        type: "scatter" as const,
        mode: "markers" as const,
        name: stems.ref.label,
        x: stems.series.x,
        y: stems.series.y,
        marker: { color, size: MARKER_SIZE, line: { width: 2, color: theme.surface } },
        showlegend: false,
        hovertemplate: "lag %{x}: %{y:.3f}<extra></extra>",
      },
    );
  }

  return {
    data,
    layout: mergeLayout(
      baseLayout(theme),
      perMark(),
      axisTitles(theme, spec.x_label, spec.y_label),
      { showlegend: false },
    ),
  };
}

export function StemChart({ spec, result }: { spec: StemChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildStem} height={280} />;
}
