import type { ResultSet, Table } from "../../../lib/types";
import { mergeLayout, perMark, type Figure } from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { HeatmapChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

export const HEATMAP_ROW_HEIGHT = 48;

/** A table's numeric body, plus whatever labels its rows and columns. */
export function matrix(table: Table): { z: (number | null)[][]; x: string[]; y: string[] } {
  // A leading blank column header means the first cell of each row is its
  // label — the shape `corr_matrix` and friends come back in.
  const labelled = table.columns[0] === "";
  const x = labelled ? table.columns.slice(1) : table.columns;
  const y = table.rows.map((row, i) => (labelled ? String(row[0]) : String(i + 1)));
  const z = table.rows.map((row) =>
    (labelled ? row.slice(1) : row).map((cell) =>
      typeof cell === "number" && Number.isFinite(cell) ? cell : null,
    ),
  );
  return { z, x, y };
}

/**
 * A matrix of values.
 *
 * The scale carries the reading. A correlation runs through a meaningful zero,
 * so it takes the diverging pair with a *neutral* midpoint — a one-hue ramp
 * over it would hide the sign, which is the whole question. A p-value grid has
 * no meaningful middle and takes the one-hue ramp. The backend rejects a spec
 * that gets this backwards; this renderer only obeys it.
 */
export function buildHeatmap(
  spec: HeatmapChartSpec,
  result: ResultSet,
  theme: ChartTheme,
): Figure {
  const table = result.tables[spec.table];
  if (!table) return { data: [], layout: mergeLayout(baseLayout(theme), perMark()) };

  const { z, x, y } = matrix(table);
  const diverging = spec.scale === "diverging";
  const colorscale: [number, string][] = diverging
    ? [
        [0, theme.diverging.low],
        [0.5, theme.diverging.mid],
        [1, theme.diverging.high],
      ]
    : theme.sequential.map((step, i) => [i / (theme.sequential.length - 1), step]);

  const [zmin, zmax] = spec.domain ?? [];

  return {
    data: [
      {
        type: "heatmap" as const,
        z,
        x,
        y,
        colorscale,
        ...(diverging ? { zmid: 0 } : {}),
        ...(zmin !== undefined ? { zmin, zmax } : {}),
        // A 2px gap in the surface colour separates the cells; a stroke around
        // each one would add ink that is not data.
        xgap: 2,
        ygap: 2,
        texttemplate: "%{z:.2f}",
        textfont: { size: 11 },
        hovertemplate: "%{y} · %{x}: %{z:.3f}<extra></extra>",
        colorbar: {
          thickness: 10,
          outlinewidth: 0,
          len: 0.9,
          tickfont: { size: 10, color: theme.textSecondary },
        },
      },
    ],
    layout: mergeLayout(baseLayout(theme), perMark(), {
      showlegend: false,
      xaxis: { type: "category" as const, showgrid: false, side: "top" as const },
      yaxis: { type: "category" as const, showgrid: false, autorange: "reversed" as const },
      margin: { l: 72, r: 20, t: 28, b: 20 },
    }),
  };
}

export function HeatmapChart({ spec, result }: { spec: HeatmapChartSpec; result: ResultSet }) {
  const rows = result.tables[spec.table]?.rows.length ?? 4;
  return (
    <PlotlyFigure
      spec={spec}
      result={result}
      build={buildHeatmap}
      height={rows * HEATMAP_ROW_HEIGHT + 80}
    />
  );
}
