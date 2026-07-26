import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  LINE_WIDTH,
  MARKER_SIZE,
  mergeLayout,
  perMark,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { ScatterChartSpec } from "../spec";
import { baseLayout, seriesColors, type ChartTheme } from "../theme";

/** Names a tool gives the intercept and the slope of a market-model fit. */
const INTERCEPTS = ["alpha", "const", "intercept"];
const SLOPES = ["beta", "mkt_rf", "slope", "market"];

/**
 * The fitted line, taken from the tool's estimates — never fitted here.
 *
 * A regression run in the browser would be a statistic computed above the tool
 * boundary, with no manifest and no way to reproduce it, which is the one thing
 * this codebase does not do. If the result carries no intercept and slope, the
 * points are drawn without a line rather than with a line nobody can trace.
 */
export function fitFromEstimates(result: ResultSet): { intercept: number; slope: number } | null {
  const find = (names: string[]) =>
    result.estimates.find((estimate) => names.includes(estimate.name.toLowerCase()));

  const intercept = find(INTERCEPTS);
  const slope = find(SLOPES);
  if (!intercept || !slope) return null;

  return { intercept: intercept.value, slope: slope.value };
}

export function buildScatter(
  spec: ScatterChartSpec,
  result: ResultSet,
  theme: ChartTheme,
): Figure {
  const x = result.series[spec.x.key];
  const y = result.series[spec.y.key];
  const colors = seriesColors(theme, [spec.y.key, ...spec.groups.map((ref) => ref.key)]);

  const data = [];
  if (x && y) {
    data.push({
      type: "scatter" as const,
      mode: "markers" as const,
      name: spec.y.label,
      x: x.y,
      y: y.y,
      marker: {
        color: colors[spec.y.key],
        size: MARKER_SIZE,
        opacity: 0.75,
        // The surface ring keeps points legible where they overlap.
        line: { width: 1.5, color: theme.surface },
      },
      hovertemplate: `${spec.x.label}: %{x:.4~g}<br>${spec.y.label}: %{y:.4~g}<extra></extra>`,
    });

    const fit = spec.fit ? fitFromEstimates(result) : null;
    if (fit) {
      const values = (x.y.filter((v) => v !== null) as number[]).sort((a, b) => a - b);
      const ends = [values[0], values.at(-1) ?? 0];
      data.push({
        type: "scatter" as const,
        mode: "lines" as const,
        name: "Fitted",
        x: ends,
        y: ends.map((v) => fit.intercept + fit.slope * v),
        line: { color: theme.textSecondary, width: LINE_WIDTH, dash: "dot" as const },
        hovertemplate: `fitted: %{y:.4~g}<extra></extra>`,
      });
    }
  }

  return {
    data,
    layout: mergeLayout(baseLayout(theme), perMark(), axisTitles(theme, spec.x.label, spec.y.label), {
      showlegend: spec.groups.length >= 2,
    }),
  };
}

export function ScatterChart({ spec, result }: { spec: ScatterChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildScatter} height={320} />;
}
