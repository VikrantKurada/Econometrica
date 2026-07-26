/**
 * The chart spec union, mirroring `backend/src/econometrica/charts/spec.py`.
 *
 * The backend owns these shapes: a Visualizer emits specs from a closed
 * vocabulary and never writes drawing code, so this file is the frontend's half
 * of that contract and nothing here may invent a field the backend cannot send.
 *
 * `Chart.test.tsx` reads the Python and fails if a type exists there and not
 * here, which is the only thing keeping the two in step.
 *
 * Note what is *absent*: no member carries a second y-axis. Two measures on two
 * scales cross at a point that is an artifact of the scaling and readers infer
 * causation from it, so the union cannot express one — that becomes `panels`.
 */

export interface SeriesRef {
  key: string;
  label: string;
}

interface ChartBase {
  title: string;
  subtitle: string;
  caption: string;
  step_id: string;
  x_label: string;
  y_label: string;
}

export interface LineChartSpec extends ChartBase {
  type: "line";
  series: SeriesRef[];
}

export interface BandChartSpec extends ChartBase {
  type: "band";
  center: SeriesRef;
  lower: SeriesRef;
  upper: SeriesRef;
}

export interface StemChartSpec extends ChartBase {
  type: "stem";
  series: SeriesRef;
  upper: SeriesRef;
  lower: SeriesRef;
}

export interface Panel {
  title: string;
  series: SeriesRef[];
  y_label: string;
}

export interface PanelsChartSpec extends ChartBase {
  type: "panels";
  panels: Panel[];
  shared_x: boolean;
}

export interface ScatterChartSpec extends ChartBase {
  type: "scatter";
  x: SeriesRef;
  y: SeriesRef;
  /** At most three: a scatter compares every pair of colours at once. */
  groups: SeriesRef[];
  fit: boolean;
}

export interface BarChartSpec extends ChartBase {
  type: "bar";
  series: SeriesRef[];
  horizontal: boolean;
}

export interface ForestChartSpec extends ChartBase {
  type: "forest";
  estimates: string[];
}

export interface HeatmapChartSpec extends ChartBase {
  type: "heatmap";
  table: string;
  scale: "sequential" | "diverging";
  domain: [number, number] | null;
}

export interface QqChartSpec extends ChartBase {
  type: "qq";
  series: SeriesRef;
  reference: "normal" | "t";
}

export interface HistogramChartSpec extends ChartBase {
  type: "histogram";
  series: SeriesRef;
  bins: number;
}

export interface AreaStackChartSpec extends ChartBase {
  type: "area_stack";
  series: SeriesRef[];
}

export interface UnderwaterChartSpec extends ChartBase {
  type: "underwater";
  series: SeriesRef;
}

export interface StatTileChartSpec extends ChartBase {
  type: "stat_tile";
  scalar: string;
  unit: string;
  precision: number;
}

export interface TableChartSpec extends ChartBase {
  type: "table";
  table: string;
}

export type ChartSpec =
  | LineChartSpec
  | BandChartSpec
  | StemChartSpec
  | PanelsChartSpec
  | ScatterChartSpec
  | BarChartSpec
  | ForestChartSpec
  | HeatmapChartSpec
  | QqChartSpec
  | HistogramChartSpec
  | AreaStackChartSpec
  | UnderwaterChartSpec
  | StatTileChartSpec
  | TableChartSpec;

export type ChartType = ChartSpec["type"];
