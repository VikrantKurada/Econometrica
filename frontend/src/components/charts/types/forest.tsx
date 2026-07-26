import type { ResultSet } from "../../../lib/types";
import { axisTitles, MARKER_SIZE, mergeLayout, perMark, type Figure } from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { ForestChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

export const FOREST_ROW_HEIGHT = 34;

/**
 * Coefficients read against zero.
 *
 * Zero is the question this chart answers, so it gets a visible rule rather
 * than a gridline: an interval crossing it is not distinguishable from no
 * effect. The estimate names sit on the axis and the numbers live in the
 * tooltip and the table — a value printed beside every point is chaos.
 */
export function buildForest(spec: ForestChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  const estimates = spec.estimates
    .map((name) => result.estimates.find((estimate) => estimate.name === name))
    .filter((estimate) => estimate !== undefined);

  const data = [
    {
      type: "scatter" as const,
      mode: "markers" as const,
      name: "Estimate",
      // Reversed so the first coefficient reads at the top.
      y: estimates.map((estimate) => estimate.name).reverse(),
      x: estimates.map((estimate) => estimate.value).reverse(),
      error_x: {
        type: "data" as const,
        symmetric: false,
        array: estimates.map((e) => (e.ci_high ?? e.value) - e.value).reverse(),
        arrayminus: estimates.map((e) => e.value - (e.ci_low ?? e.value)).reverse(),
        color: theme.series[0],
        thickness: 1.5,
        width: 4,
      },
      marker: {
        color: theme.series[0],
        size: MARKER_SIZE,
        line: { width: 2, color: theme.surface },
      },
      hovertemplate: "%{y}: %{x:.4~g}<extra></extra>",
    },
  ];

  return {
    data,
    layout: mergeLayout(
      baseLayout(theme),
      perMark(),
      axisTitles(theme, spec.x_label || "Estimate", spec.y_label),
      {
        showlegend: false,
        xaxis: { zeroline: true, zerolinecolor: theme.textSecondary, zerolinewidth: 1 },
        yaxis: { type: "category" as const, automargin: true },
        margin: { l: 96, r: 24, t: 8, b: 40 },
      },
    ),
  };
}

export function ForestChart({ spec, result }: { spec: ForestChartSpec; result: ResultSet }) {
  return (
    <PlotlyFigure
      spec={spec}
      result={result}
      build={buildForest}
      height={spec.estimates.length * FOREST_ROW_HEIGHT + 80}
    />
  );
}
