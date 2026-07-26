import type { ResultSet } from "../../../lib/types";
import { axisTitles, BAR_GAP, bind, mergeLayout, perMark, type Figure } from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { BarChartSpec } from "../spec";
import { baseLayout, seriesColors, type ChartTheme } from "../theme";

/**
 * Magnitudes across categories.
 *
 * Every bar of a series wears the same hue: colouring bars by their own value
 * would spend the identity channel re-encoding what the bar length already
 * shows. The value rides the cap as a direct label, so nothing here is
 * reachable only by hovering.
 */
export function buildBar(spec: BarChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  const colors = seriesColors(theme, spec.series.map((ref) => ref.key));
  const bound = bind(result, ...spec.series);

  const data = bound.map(({ ref, series }) => ({
    type: "bar" as const,
    name: ref.label,
    orientation: spec.horizontal ? ("h" as const) : ("v" as const),
    x: spec.horizontal ? series.y : series.x,
    y: spec.horizontal ? series.x : series.y,
    marker: { color: colors[ref.key] },
    // Clean numbers: `toPrecision` alone prints 0.970 beside 1.04, which reads
    // as two different measurements taken to two different accuracies.
    text: series.y.map((value) =>
      value === null ? "" : String(Number.parseFloat(value.toPrecision(3))),
    ),
    textposition: "outside" as const,
    textfont: { color: theme.textSecondary, size: 11 },
    cliponaxis: false,
    hovertemplate: "%{x}: %{y:.4~g}<extra></extra>",
  }));

  return {
    data,
    layout: mergeLayout(
      baseLayout(theme),
      perMark(),
      axisTitles(theme, spec.x_label, spec.y_label),
      {
        showlegend: bound.length >= 2,
        // The band's leftover is air, and the gap between neighbours is the
        // surface showing through rather than a stroke drawn around a mark.
        bargap: BAR_GAP,
        bargroupgap: 0.08,
        [spec.horizontal ? "yaxis" : "xaxis"]: { type: "category" as const },
      },
    ),
  };
}

export function BarChart({ spec, result }: { spec: BarChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildBar} height={280} />;
}
