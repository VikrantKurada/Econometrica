import type { Data, Layout } from "plotly.js";

import type { ResultSet } from "../../../lib/types";
import { axisTitles, bind, LINE_WIDTH, mergeLayout, valueRow, type Figure } from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { PanelsChartSpec } from "../spec";
import { baseLayout, seriesColors, type ChartTheme } from "../theme";

/** Vertical air between panels, as a fraction of the plot area. */
const PANEL_GAP = 0.1;
export const PANEL_HEIGHT = 150;

/**
 * Several measures stacked on one x-axis.
 *
 * This is what a two-scale overlay becomes. Each panel keeps its own y-axis
 * because the measures have different units — but they are separate plots with
 * separate domains, not two scales sharing a plot area, so there is no crossing
 * point for a reader to over-read. The x-axes are `matches`-linked, which pans
 * and zooms them together and puts one crosshair across the stack.
 *
 * The bottom panel owns the bare `x`/`y` axes so it carries the tick labels;
 * the ones above are suffixed and hide theirs.
 */
export function buildPanels(spec: PanelsChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  const count = spec.panels.length;
  const height = (1 - PANEL_GAP * (count - 1)) / count;

  // Colour is assigned across the whole chart in declaration order, so no two
  // panels share a hue and the adjacent-pair floors are the ones that govern.
  const colors = seriesColors(
    theme,
    spec.panels.flatMap((panel) => panel.series.map((ref) => ref.key)),
  );

  const base = baseLayout(theme);
  const layout: Record<string, unknown> = {
    ...base,
    ...axisTitles(theme, spec.x_label, ""),
    hovermode: "x unified",
    showlegend: spec.panels.some((panel) => panel.series.length >= 2),
    annotations: [] as unknown[],
  };
  const data: Data[] = [];
  const titles: unknown[] = [];

  spec.panels.forEach((panel, index) => {
    const fromBottom = count - 1 - index;
    const bottom = fromBottom * (height + PANEL_GAP);
    // The bottom panel is the unsuffixed pair; suffixes climb from there.
    const suffix = fromBottom === 0 ? "" : String(fromBottom + 1);

    layout[`yaxis${suffix}`] = {
      ...base.yaxis,
      domain: [bottom, bottom + height],
      title: panel.y_label
        ? { text: panel.y_label, font: { size: 11, color: theme.textSecondary } }
        : undefined,
    };
    layout[`xaxis${suffix}`] = {
      ...base.xaxis,
      ...(suffix ? { matches: "x", showticklabels: false } : (layout.xaxis as object)),
      anchor: `y${suffix}`,
      showspikes: true,
      spikemode: "across",
      spikethickness: 1,
      spikedash: "solid",
      spikecolor: theme.textSecondary,
    };

    // The panel title is what names the series, which is why a stack of
    // single-series panels needs no legend.
    titles.push({
      text: panel.title,
      xref: "paper",
      yref: "paper",
      x: 0,
      y: Math.min(1, bottom + height + 0.02),
      xanchor: "left",
      yanchor: "bottom",
      showarrow: false,
      font: { size: 11, color: theme.textPrimary },
    });

    for (const { ref, series } of bind(result, ...panel.series)) {
      data.push({
        type: "scatter" as const,
        mode: "lines" as const,
        name: ref.label,
        x: series.x,
        y: series.y,
        xaxis: `x${suffix}`,
        yaxis: `y${suffix}`,
        line: { color: colors[ref.key], width: LINE_WIDTH },
        hovertemplate: valueRow(),
      });
    }
  });

  layout.annotations = titles;
  // The titles sit above each panel, so the top one needs headroom.
  layout.margin = { l: 56, r: 20, t: 24, b: 40 };

  return { data, layout: mergeLayout(layout as Partial<Layout>) };
}

export function PanelsChart({ spec, result }: { spec: PanelsChartSpec; result: ResultSet }) {
  return (
    <PlotlyFigure
      spec={spec}
      result={result}
      build={buildPanels}
      height={spec.panels.length * PANEL_HEIGHT + 60}
    />
  );
}
