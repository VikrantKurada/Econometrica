import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  bind,
  crosshair,
  endLabel,
  LINE_WIDTH,
  mergeLayout,
  valueRow,
  wash,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { BandChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

/**
 * An estimate and its confidence ribbon.
 *
 * One entity, three series — so no legend box: the title names what is drawn
 * and a swatch would only restate it. The interval still reads in the tooltip
 * and in the table view, because a band a reader cannot put numbers to is
 * decoration.
 */
export function buildBand(spec: BandChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  const color = theme.series[0];
  const [center] = bind(result, spec.center);
  const [lower] = bind(result, spec.lower);
  const [upper] = bind(result, spec.upper);

  const data = [];
  if (lower && upper) {
    // The ribbon is drawn first so the estimate sits on top of its own band.
    data.push(
      {
        type: "scatter" as const,
        mode: "lines" as const,
        name: spec.lower.label,
        x: lower.series.x,
        y: lower.series.y,
        line: { width: 0, color },
        showlegend: false,
        hovertemplate: valueRow(),
      },
      {
        type: "scatter" as const,
        mode: "lines" as const,
        name: spec.upper.label,
        x: upper.series.x,
        y: upper.series.y,
        line: { width: 0, color },
        fill: "tonexty" as const,
        fillcolor: wash(color, 0.16),
        showlegend: false,
        hovertemplate: valueRow(),
      },
    );
  }
  if (center) {
    data.push({
      type: "scatter" as const,
      mode: "lines" as const,
      name: center.ref.label,
      x: center.series.x,
      y: center.series.y,
      line: { color, width: LINE_WIDTH },
      showlegend: false,
      hovertemplate: valueRow(),
    });
  }

  const label = center ? endLabel(center, theme) : null;

  return {
    data,
    layout: mergeLayout(
      baseLayout(theme),
      crosshair(theme),
      axisTitles(theme, spec.x_label, spec.y_label),
      {
        showlegend: false,
        annotations: label ? [label] : [],
        margin: { l: 56, r: 96, t: 8, b: 40 },
      },
    ),
  };
}

export function BandChart({ spec, result }: { spec: BandChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildBand} height={300} />;
}
