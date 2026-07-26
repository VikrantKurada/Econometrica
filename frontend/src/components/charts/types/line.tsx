import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  bind,
  crosshair,
  directLabels,
  LINE_WIDTH,
  mergeLayout,
  valueRow,
  type Bound,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { LineChartSpec } from "../spec";
import { baseLayout, seriesColors, type ChartTheme } from "../theme";

export function buildLine(spec: LineChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  // Colours come from the declared order, not from what happens to bind, so a
  // series keeps its hue when another one is missing.
  const colors = seriesColors(theme, spec.series.map((ref) => ref.key));
  const bound = bind(result, ...spec.series);

  const data = bound.map(({ ref, series }) => ({
    type: "scatter" as const,
    mode: "lines" as const,
    name: ref.label,
    x: series.x,
    y: series.y,
    line: { color: colors[ref.key], width: LINE_WIDTH, shape: "linear" as const },
    hovertemplate: valueRow(),
  }));

  return {
    data,
    layout: lineLayout(theme, bound, spec.x_label, spec.y_label),
  };
}

/** Shared with the other multi-series time charts, which read the same way. */
export function lineLayout(
  theme: ChartTheme,
  bound: Bound[],
  xLabel: string,
  yLabel: string,
): Figure["layout"] {
  const annotations = directLabels(bound, theme);

  return mergeLayout(
    baseLayout(theme),
    crosshair(theme),
    axisTitles(theme, xLabel, yLabel),
    {
      showlegend: bound.length >= 2,
      annotations,
      // Room for the end labels; without it they render outside the plot area.
      margin: { l: 56, r: annotations.length ? 96 : 20, t: 8, b: 40 },
    },
  );
}

export function LineChart({ spec, result }: { spec: LineChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildLine} height={300} />;
}
