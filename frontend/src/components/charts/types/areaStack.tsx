import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  bind,
  crosshair,
  directLabels,
  LINE_WIDTH,
  mergeLayout,
  valueRow,
  wash,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { AreaStackChartSpec } from "../spec";
import { baseLayout, seriesColors, type ChartTheme } from "../theme";

/**
 * Shares summing to a whole — a variance decomposition, regime probabilities.
 *
 * Each band is a wash with its own boundary drawn on top in the series colour:
 * the boundary is what separates neighbouring bands, so no stroke is drawn
 * around a fill and no band is a saturated block.
 */
export function buildAreaStack(
  spec: AreaStackChartSpec,
  result: ResultSet,
  theme: ChartTheme,
): Figure {
  const colors = seriesColors(theme, spec.series.map((ref) => ref.key));
  const bound = bind(result, ...spec.series);

  const data = bound.map(({ ref, series }) => ({
    type: "scatter" as const,
    mode: "lines" as const,
    name: ref.label,
    x: series.x,
    y: series.y,
    stackgroup: "one",
    fillcolor: wash(colors[ref.key], 0.22),
    line: { color: colors[ref.key], width: LINE_WIDTH },
    hovertemplate: valueRow(".3f"),
  }));

  // A band is drawn at its cumulative height, so its label has to be placed
  // there too — at its own value it would detach from the band it names.
  const running: number[] = [];
  const stacked = bound.map(({ ref, series }) => ({
    ref,
    series: {
      ...series,
      y: series.y.map((value, i) => {
        running[i] = (running[i] ?? 0) + (value ?? 0);
        return running[i];
      }),
    },
  }));

  const annotations = directLabels(stacked, theme);

  return {
    data,
    layout: mergeLayout(
      baseLayout(theme),
      crosshair(theme),
      axisTitles(theme, spec.x_label, spec.y_label),
      {
        showlegend: bound.length >= 2,
        annotations,
        margin: { l: 56, r: 96, t: 8, b: 40 },
      },
    ),
  };
}

export function AreaStackChart({
  spec,
  result,
}: {
  spec: AreaStackChartSpec;
  result: ResultSet;
}) {
  return <PlotlyFigure spec={spec} result={result} build={buildAreaStack} height={300} />;
}
