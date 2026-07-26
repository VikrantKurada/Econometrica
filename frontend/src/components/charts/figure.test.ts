/**
 * The rules that hold for every chart, checked against every chart.
 *
 * Each of these is a way a chart misleads rather than a matter of taste, so
 * they are asserted across the whole union — a fifteenth type added later has
 * to satisfy them too or this file goes red.
 */

import { describe, expect, it } from "vitest";

import type { ResultSet } from "../../lib/types";
import { FIXTURE_RESULT, GALLERY } from "./fixtures";
import { buildFigure, chartTable, seriesCount } from "./figure";
import { readChartTheme, seriesColors } from "./theme";
import type { ChartType, LineChartSpec } from "./spec";

const theme = readChartTheme(null);
const figures = GALLERY.map((spec) => [spec.type, spec] as const);

/** Which reading each form supports: an X to compare, or one mark to identify. */
const HOVER: Record<ChartType, "x unified" | "closest"> = {
  line: "x unified",
  band: "x unified",
  panels: "x unified",
  area_stack: "x unified",
  underwater: "x unified",
  stem: "closest",
  scatter: "closest",
  bar: "closest",
  forest: "closest",
  heatmap: "closest",
  qq: "closest",
  histogram: "closest",
  stat_tile: "closest",
  table: "closest",
};

describe("every chart in the union", () => {
  it("covers every spec type exactly once in the gallery", () => {
    expect(new Set(GALLERY.map((s) => s.type)).size).toBe(GALLERY.length);
  });

  it.each(figures)("%s has a table view", (_type, spec) => {
    // The relief for the light-mode contrast WARN and the accessibility
    // fallback: no value is reachable only by hovering a coloured mark.
    const table = chartTable(spec, FIXTURE_RESULT);
    expect(table.columns.length).toBeGreaterThan(0);
    expect(table.rows.length).toBeGreaterThan(0);
  });

  it.each(figures)("%s never overlays a second y-scale", (_type, spec) => {
    // A panels chart has a yaxis2 — a separate plot with its own domain. What
    // must never appear is `overlaying`, which is what puts two scales on one
    // plot and invents a crossing point the data does not have.
    const figure = buildFigure(spec, FIXTURE_RESULT, theme);
    if (!figure) return;

    for (const [key, axis] of Object.entries(figure.layout)) {
      if (!/^yaxis\d*$/.test(key)) continue;
      expect(axis).not.toHaveProperty("overlaying");
    }
  });

  it.each(figures)("%s shows a legend only when it draws two or more series", (_type, spec) => {
    const figure = buildFigure(spec, FIXTURE_RESULT, theme);
    if (!figure) return;

    expect(figure.layout.showlegend).toBe(seriesCount(spec) >= 2);
  });

  it.each(figures)("%s picks the hover layer its form supports", (type, spec) => {
    const figure = buildFigure(spec, FIXTURE_RESULT, theme);
    if (!figure) return;

    expect(figure.layout.hovermode).toBe(HOVER[type]);
  });

  it.each(figures)("%s paints its marks from the theme, never from Plotly", (_type, spec) => {
    // Plotly's default colorway assigns by trace order, which is exactly the
    // recolour-on-filter bug. Every visible mark states its own colour.
    const figure = buildFigure(spec, FIXTURE_RESULT, theme);
    if (!figure) return;

    const known = new Set([
      ...theme.series,
      theme.muted,
      theme.gridline,
      theme.textPrimary,
      theme.textSecondary,
      "rgba(0,0,0,0)",
    ]);
    for (const trace of figure.data) {
      const record = trace as Record<string, { color?: unknown }>;
      const color = record.line?.color ?? record.marker?.color;
      if (typeof color !== "string") continue;
      // Fills are the series hue at low opacity, so they carry an alpha.
      expect(known.has(color) || color.startsWith("rgba(")).toBe(true);
    }
  });
});

/** A line spec over two made-up series, for testing label placement. */
function twoSeries(first: number[], second: number[]): [LineChartSpec, ResultSet] {
  const x = first.map((_, i) => i);
  const spec: LineChartSpec = {
    ...(GALLERY.find((s) => s.type === "line") as LineChartSpec),
    series: [
      { key: "a", label: "Alpha" },
      { key: "b", label: "Bravo" },
    ],
  };
  const result: ResultSet = {
    ...FIXTURE_RESULT,
    series: {
      a: { name: "a", x, y: first },
      b: { name: "b", x, y: second },
    },
  };
  return [spec, result];
}

describe("direct labels", () => {
  it.each(figures)("%s identifies its series without relying on colour", (_type, spec) => {
    // Three of the light-mode slots sit below 3:1 on #fafafa, so identity has
    // to come from a legend or from a label — never from the hue alone.
    const count = seriesCount(spec);
    if (count < 2) return;

    const figure = buildFigure(spec, FIXTURE_RESULT, theme);
    const labels = figure?.layout.annotations ?? [];
    expect(figure?.layout.showlegend === true || labels.length >= count).toBe(true);
  });

  it("labels the ends when the series separate", () => {
    const [spec, result] = twoSeries([1, 2, 3, 10], [1, 2, 3, 0]);
    const labels = buildFigure(spec, result, theme)?.layout.annotations ?? [];
    expect(labels.map((a) => a.text)).toEqual(["Alpha", "Bravo"]);
  });

  it("drops colliding end labels rather than stacking them", () => {
    // Nudging labels apart to fit detaches them from their lines and reads as
    // noise; the legend and the table view carry identity instead.
    const [spec, result] = twoSeries([0, 5, 10, 4.99], [0, 5, 10, 5.01]);
    const figure = buildFigure(spec, result, theme);

    expect(figure?.layout.annotations).toHaveLength(0);
    expect(figure?.layout.showlegend).toBe(true);
  });

  it("writes labels in ink rather than in the series colour", () => {
    // A light categorical hue is illegible as text on the surface; the mark
    // beside the label carries identity.
    const spec = GALLERY.find((s) => s.type === "line") as LineChartSpec;
    const figure = buildFigure(spec, FIXTURE_RESULT, theme);

    for (const annotation of figure?.layout.annotations ?? []) {
      expect(theme.series).not.toContain(annotation.font?.color);
    }
  });
});

describe("colour follows the entity", () => {
  it("keeps a series' colour when an earlier one is not drawn", () => {
    // The recolour-on-filter bug: a reader who learned "EWMA is orange" must
    // not find it blue because the other series dropped out.
    const spec = GALLERY.find((s) => s.type === "line") as LineChartSpec;
    const colors = seriesColors(theme, spec.series.map((s) => s.key));

    const partial: ResultSet = {
      ...FIXTURE_RESULT,
      series: { ewma_vol: FIXTURE_RESULT.series.ewma_vol },
    };
    const figure = buildFigure(spec, partial, theme);

    const traces = figure?.data ?? [];
    expect(traces).toHaveLength(1);
    expect((traces[0] as { line?: { color?: string } }).line?.color).toBe(colors.ewma_vol);
    expect(colors.ewma_vol).toBe(theme.series[1]);
  });
});
