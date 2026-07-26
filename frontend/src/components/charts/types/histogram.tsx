import type { ResultSet } from "../../../lib/types";
import { axisTitles, mergeLayout, perMark, type Figure } from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { HistogramChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

/** One series' distribution. Binning is a display choice, so the spec carries it. */
export function buildHistogram(
  spec: HistogramChartSpec,
  result: ResultSet,
  theme: ChartTheme,
): Figure {
  const series = result.series[spec.series.key];

  return {
    data: [
      {
        type: "histogram" as const,
        name: spec.series.label,
        x: series?.y ?? [],
        marker: { color: theme.series[0] },
        hovertemplate: "%{x}: %{y}<extra></extra>",
        // Plotly takes nbinsx; its published typings do not list it.
        ...({ nbinsx: spec.bins } as object),
      },
    ],
    layout: mergeLayout(
      baseLayout(theme),
      perMark(),
      axisTitles(theme, spec.x_label || spec.series.label, spec.y_label || "Count"),
      {
        showlegend: false,
        // Neighbouring bins read as distinct because of the surface showing
        // between them, not because of a stroke around each bar.
        bargap: 0.04,
      },
    ),
  };
}

export function HistogramChart({ spec, result }: { spec: HistogramChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildHistogram} height={280} />;
}
