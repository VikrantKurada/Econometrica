/**
 * Spec in, figure out — and the table twin that goes with it.
 *
 * The switches here are exhaustive by construction: the `never` in each default
 * arm makes a fifteenth spec type a type error rather than a chart that
 * silently fails to draw.
 */

import type { ResultSet } from "../../lib/types";
import type { Figure, TableData } from "./marks";
import type { ChartSpec, SeriesRef } from "./spec";
import type { ChartTheme } from "./theme";
import { buildAreaStack } from "./types/areaStack";
import { buildBand } from "./types/band";
import { buildBar } from "./types/bar";
import { buildForest } from "./types/forest";
import { buildHeatmap, matrix } from "./types/heatmap";
import { buildHistogram } from "./types/histogram";
import { buildLine } from "./types/line";
import { buildPanels } from "./types/panels";
import { buildQq } from "./types/qq";
import { buildScatter } from "./types/scatter";
import { buildStem } from "./types/stem";
import { buildUnderwater } from "./types/underwater";

/** `null` for the two types that are HTML rather than a plot. */
export function buildFigure(
  spec: ChartSpec,
  result: ResultSet,
  theme: ChartTheme,
): Figure | null {
  switch (spec.type) {
    case "line":
      return buildLine(spec, result, theme);
    case "band":
      return buildBand(spec, result, theme);
    case "stem":
      return buildStem(spec, result, theme);
    case "panels":
      return buildPanels(spec, result, theme);
    case "scatter":
      return buildScatter(spec, result, theme);
    case "bar":
      return buildBar(spec, result, theme);
    case "forest":
      return buildForest(spec, result, theme);
    case "heatmap":
      return buildHeatmap(spec, result, theme);
    case "qq":
      return buildQq(spec, result, theme);
    case "histogram":
      return buildHistogram(spec, result, theme);
    case "area_stack":
      return buildAreaStack(spec, result, theme);
    case "underwater":
      return buildUnderwater(spec, result, theme);
    case "stat_tile":
    case "table":
      return null;
    default: {
      const unreachable: never = spec;
      return unreachable;
    }
  }
}

/**
 * How many series the chart draws, which is what decides the legend.
 *
 * A band is one entity wearing three series, and a stack of single-series
 * panels is identified by its panel titles — in both cases a legend box would
 * restate what the chart already says.
 */
export function seriesCount(spec: ChartSpec): number {
  switch (spec.type) {
    case "line":
    case "bar":
    case "area_stack":
      return spec.series.length;
    case "panels":
      return Math.max(...spec.panels.map((panel) => panel.series.length));
    case "scatter":
      return spec.groups.length || 1;
    default:
      return 1;
  }
}

/** Why this chart cannot be drawn, in words a reader can act on. */
export function chartProblem(spec: ChartSpec, result: ResultSet): string | null {
  const missing = (refs: SeriesRef[]): string[] =>
    refs.filter((ref) => !(ref.key in result.series)).map((ref) => ref.key);

  let absent: string[] = [];
  switch (spec.type) {
    case "line":
    case "bar":
    case "area_stack":
      absent = missing(spec.series);
      break;
    case "band":
      absent = missing([spec.center, spec.lower, spec.upper]);
      break;
    case "stem":
      absent = missing([spec.series, spec.upper, spec.lower]);
      break;
    case "panels":
      absent = missing(spec.panels.flatMap((panel) => panel.series));
      break;
    case "scatter":
      absent = missing([spec.x, spec.y, ...spec.groups]);
      break;
    case "qq":
      // The spec can name a t reference, but nothing on the wire carries the
      // degrees of freedom it would need. Better to say so than to draw normal
      // quantiles under a label claiming otherwise.
      if (spec.reference !== "normal") return "a t reference needs degrees of freedom the result does not carry";
      absent = missing([spec.series]);
      break;
    case "histogram":
    case "underwater":
      absent = missing([spec.series]);
      break;
    case "forest":
      absent = spec.estimates.filter(
        (name) => !result.estimates.some((estimate) => estimate.name === name),
      );
      break;
    case "heatmap":
    case "table":
      absent = spec.table in result.tables ? [] : [spec.table];
      break;
    case "stat_tile":
      absent = spec.scalar in result.scalars ? [] : [spec.scalar];
      break;
    default: {
      const unreachable: never = spec;
      return unreachable;
    }
  }

  return absent.length ? `the result carries no ${absent.join(", ")}` : null;
}

function seriesTable(result: ResultSet, xLabel: string, refs: SeriesRef[]): TableData {
  const bound = refs.map((ref) => ({ ref, series: result.series[ref.key] })).filter((b) => b.series);
  const index = bound[0]?.series.x ?? [];

  return {
    columns: [xLabel || "x", ...bound.map(({ ref }) => ref.label)],
    rows: index.map((x, i) => [x, ...bound.map(({ series }) => series.y[i] ?? null)]),
  };
}

/**
 * Every chart's values, as a table.
 *
 * There is no arm here that returns nothing: a chart without a table view is a
 * chart whose numbers are only reachable by hovering a coloured mark, which is
 * both the accessibility failure and the thing the light-mode contrast WARN
 * makes non-negotiable.
 */
export function chartTable(spec: ChartSpec, result: ResultSet): TableData {
  switch (spec.type) {
    case "line":
    case "bar":
    case "area_stack":
      return seriesTable(result, spec.x_label, spec.series);
    case "band":
      return seriesTable(result, spec.x_label, [spec.center, spec.lower, spec.upper]);
    case "stem":
      return seriesTable(result, spec.x_label || "lag", [spec.series, spec.upper, spec.lower]);
    case "panels":
      return seriesTable(result, spec.x_label, spec.panels.flatMap((panel) => panel.series));
    case "scatter":
      return seriesTable(result, spec.x.label, [spec.x, spec.y, ...spec.groups]);
    case "qq":
    case "histogram":
    case "underwater":
      return seriesTable(result, spec.x_label, [spec.series]);
    case "forest":
      return {
        columns: ["estimate", "value", "ci low", "ci high", "p value"],
        rows: spec.estimates
          .map((name) => result.estimates.find((estimate) => estimate.name === name))
          .filter((estimate) => estimate !== undefined)
          .map((e) => [e.name, e.value, e.ci_low, e.ci_high, e.p_value]),
      };
    case "heatmap": {
      const table = result.tables[spec.table];
      if (!table) return { columns: [], rows: [] };
      const { z, x, y } = matrix(table);
      return { columns: ["", ...x], rows: z.map((row, i) => [y[i], ...row]) };
    }
    case "table": {
      const table = result.tables[spec.table];
      return table ? { columns: table.columns, rows: table.rows } : { columns: [], rows: [] };
    }
    case "stat_tile":
      return {
        columns: [spec.title, spec.unit || "value"],
        rows: [[spec.scalar, result.scalars[spec.scalar] ?? null]],
      };
    default: {
      const unreachable: never = spec;
      return unreachable;
    }
  }
}
