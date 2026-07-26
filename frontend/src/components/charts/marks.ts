/**
 * The mark specs, in one place.
 *
 * These numbers are the `dataviz` skill's fixed specs rather than choices this
 * project gets to make per chart: thin marks, hairline chrome, and white doing
 * the separating. The data is the only thing allowed to be loud.
 */

import type { Annotations, Data, Layout } from "plotly.js";

import type { ResultSet, Series } from "../../lib/types";
import type { SeriesRef } from "./spec";
import type { ChartTheme } from "./theme";

export interface Figure {
  data: Data[];
  layout: Partial<Layout>;
}

/** The table twin every chart carries. */
export interface TableData {
  columns: string[];
  rows: unknown[][];
}

export const LINE_WIDTH = 2;
export const MARKER_SIZE = 8;
/** An area fill is a wash, never a saturated block. */
export const FILL_OPACITY = 0.1;
/**
 * Leftover band width is air, so a bar never fills its slot. The spec caps a
 * bar at 24px; Plotly sizes bars in data units and cannot be told a pixel
 * ceiling, so this gap is the closest available control — at a typical card
 * width it lands a handful of categories just under the cap.
 */
export const BAR_GAP = 0.6;

/** A series and the label the spec wants on it, or nothing if it does not bind. */
export interface Bound {
  ref: SeriesRef;
  series: Series;
}

export function bind(result: ResultSet, ...refs: SeriesRef[]): Bound[] {
  return refs
    .map((ref) => ({ ref, series: result.series[ref.key] }))
    .filter((bound): bound is Bound => bound.series !== undefined);
}

/** The series hue as a wash. Fills carry alpha; strokes never do. */
export function wash(hex: string, opacity = FILL_OPACITY): string {
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => Number.parseInt(value.slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${opacity})`;
}

/** The last point that exists, which is where an end label goes. */
export function lastPoint(series: Series): { x: unknown; y: number } | null {
  for (let i = series.y.length - 1; i >= 0; i -= 1) {
    const y = series.y[i];
    if (y !== null && Number.isFinite(y)) return { x: series.x[i], y };
  }
  return null;
}

/**
 * A direct label riding the end of a series.
 *
 * In ink, never in the series colour: a light categorical hue is illegible as
 * text on the surface, so identity comes from the mark the label sits beside.
 */
export function endLabel(bound: Bound, theme: ChartTheme, axis = ""): Partial<Annotations> | null {
  const point = lastPoint(bound.series);
  if (!point) return null;

  return {
    x: point.x as number,
    y: point.y,
    xref: `x${axis}` as "x",
    yref: `y${axis}` as "y",
    text: bound.ref.label,
    showarrow: false,
    xanchor: "left",
    xshift: 8,
    font: { size: 11, color: theme.textSecondary },
  };
}

/** Four or fewer series get direct labels as well as a legend. */
export const DIRECT_LABEL_LIMIT = 4;

/**
 * Two end labels closer than this share of the y range would overlap. A label
 * is ~11px against a plot of ~250px, so this is that ratio with room to spare.
 */
const MIN_LABEL_SEPARATION = 0.06;

/**
 * End labels for a set of series — or none, when they would collide.
 *
 * Where lines converge, nudging the labels apart to make them fit detaches
 * them from the lines they name and reads as noise. The documented fallback is
 * the legend plus the tooltip, both of which are already there, and the table
 * view keeps every value reachable either way.
 */
export function directLabels(
  bound: Bound[],
  theme: ChartTheme,
  axis = "",
): Partial<Annotations>[] {
  if (bound.length > DIRECT_LABEL_LIMIT) return [];

  const labels = bound
    .map((series) => endLabel(series, theme, axis))
    .filter((label) => label !== null);
  if (labels.length < 2) return labels;

  const values = bound.flatMap(
    (series) => series.series.y.filter((y) => y !== null) as number[],
  );
  const span = Math.max(...values) - Math.min(...values);
  if (span === 0) return [];

  const ends = labels.map((label) => label.y as number).sort((a, b) => a - b);
  const collides = ends.some(
    (y, i) => i > 0 && Math.abs(y - ends[i - 1]) < span * MIN_LABEL_SEPARATION,
  );

  return collides ? [] : labels;
}

/**
 * Crosshair and one tooltip listing every series at that x.
 *
 * Readers aim at a date, never at a 2px line, so the pointer only has to be
 * near the right x — it never has to land on a mark.
 */
export function crosshair(theme: ChartTheme): Partial<Layout> {
  return {
    hovermode: "x unified",
    xaxis: {
      showspikes: true,
      spikemode: "across",
      spikethickness: 1,
      spikedash: "solid",
      spikecolor: theme.textSecondary,
    },
  };
}

/** Per-mark hover, for forms where the mark itself is the hit target. */
export function perMark(): Partial<Layout> {
  return { hovermode: "closest" };
}

/**
 * A tooltip row: the value leads in strong ink, the series name follows.
 *
 * That is the legend's hierarchy inverted, because here the reader already has
 * the series and wants the number. The name has to be *there*, though — under
 * a shared crosshair the rows are otherwise told apart by their line keys
 * alone, which is identity by colour.
 */
export function valueRow(format = ".4~g"): string {
  return `<b>%{y:${format}}</b>  %{fullData.name}<extra></extra>`;
}

/** Axis titles, in the muted ink the rest of the chrome wears. */
export function axisTitles(
  theme: ChartTheme,
  x: string,
  y: string,
): { xaxis: Partial<Layout["xaxis"]>; yaxis: Partial<Layout["yaxis"]> } {
  const font = { size: 11, color: theme.textSecondary };
  return {
    xaxis: x ? { title: { text: x, font } } : {},
    yaxis: y ? { title: { text: y, font } } : {},
  };
}

/**
 * Merge layout fragments a level deep, so an axis fragment adds to the base
 * axis rather than replacing it and silently dropping the gridline spec.
 */
export function mergeLayout(...parts: Partial<Layout>[]): Partial<Layout> {
  const merged: Record<string, unknown> = {};
  for (const part of parts) {
    for (const [key, value] of Object.entries(part)) {
      const existing = merged[key];
      const mergeable =
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value) &&
        existing !== null &&
        typeof existing === "object" &&
        !Array.isArray(existing);
      merged[key] = mergeable ? { ...(existing as object), ...(value as object) } : value;
    }
  }
  return merged as Partial<Layout>;
}
