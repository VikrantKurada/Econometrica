import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  bind,
  crosshair,
  LINE_WIDTH,
  mergeLayout,
  valueRow,
  wash,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { UnderwaterChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

/**
 * Drawdown, filled down from zero.
 *
 * It wears slot 1 like any other single-series chart, not red. Red here would
 * be a status colour doing a series' job — hue is identity in this system, and
 * "this is a loss" is already carried by the title, the axis and the fact that
 * every value is negative.
 */
export function buildUnderwater(
  spec: UnderwaterChartSpec,
  result: ResultSet,
  theme: ChartTheme,
): Figure {
  const color = theme.series[0];
  const [bound] = bind(result, spec.series);

  return {
    data: bound
      ? [
          {
            type: "scatter" as const,
            mode: "lines" as const,
            name: bound.ref.label,
            x: bound.series.x,
            y: bound.series.y,
            fill: "tozeroy" as const,
            fillcolor: wash(color, 0.14),
            line: { color, width: LINE_WIDTH },
            hovertemplate: valueRow(".2%"),
          },
        ]
      : [],
    layout: mergeLayout(
      baseLayout(theme),
      crosshair(theme),
      axisTitles(theme, spec.x_label, spec.y_label || "Depth"),
      { showlegend: false, yaxis: { tickformat: ".0%" } },
    ),
  };
}

export function UnderwaterChart({
  spec,
  result,
}: {
  spec: UnderwaterChartSpec;
  result: ResultSet;
}) {
  return <PlotlyFigure spec={spec} result={result} build={buildUnderwater} height={260} />;
}
