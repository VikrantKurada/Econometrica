/**
 * The one place a chart learns what it looks like.
 *
 * Every colour is read from a CSS custom property at render time, so the theme
 * toggle repaints the charts through the same mechanism as the rest of the UI —
 * no chart keeps its own copy of the palette and no component subscribes to the
 * theme store. Where no stylesheet is loaded (jsdom, an exported artifact) the
 * validated hexes in `palette.ts` stand in.
 */

import type { Config, Layout } from "plotly.js";

import { toPlotlyColor } from "./color";
import {
  DIVERGING_DARK,
  DIVERGING_LIGHT,
  SEQUENTIAL_BLUE,
  SERIES_DARK,
  SERIES_LIGHT,
} from "./palette";

export interface ChartTheme {
  mode: "light" | "dark";
  surface: string;
  gridline: string;
  textPrimary: string;
  textSecondary: string;
  /** Where a ninth series folds to. Grey is "Other", not a hue. */
  muted: string;
  series: string[];
  sequential: string[];
  diverging: { low: string; mid: string; high: string };
  fontFamily: string;
}

const FALLBACK_INK = {
  light: { primary: "#333333", secondary: "#808080", border: "#e5e5e5", surface: "#fafafa" },
  dark: { primary: "#f2f2f2", secondary: "#a6a6a6", border: "#43474b", surface: "#121416" },
} as const;

const SANS = 'Inter Variable, Inter, ui-sans-serif, system-ui, sans-serif';

function resolvedMode(element: Element | null): "light" | "dark" {
  const stamped = element?.ownerDocument ?? globalThis.document;
  return stamped?.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

/**
 * Reads the theme off `element`, which must be inside the chart so it inherits
 * the tokens. Falls back per token rather than wholesale: a stylesheet that
 * declares some of them still wins for those.
 */
export function readChartTheme(element: Element | null): ChartTheme {
  const mode = resolvedMode(element);
  const ink = FALLBACK_INK[mode];
  const slots = mode === "dark" ? SERIES_DARK : SERIES_LIGHT;

  const styles = element ? getComputedStyle(element) : null;
  const token = (name: string, fallback: string): string =>
    toPlotlyColor(styles?.getPropertyValue(name) ?? "", fallback);

  return {
    mode,
    surface: token("--surface-1", ink.surface),
    gridline: token("--border", ink.border),
    textPrimary: token("--text-primary", ink.primary),
    textSecondary: token("--text-secondary", ink.secondary),
    muted: token("--text-secondary", ink.secondary),
    series: slots.map((slot, index) => token(`--series-${index + 1}`, slot)),
    // The sequential ramp anchors "near zero" at the surface, so dark mode
    // reads it from the dark end rather than glowing where there is no data.
    sequential: mode === "dark" ? [...SEQUENTIAL_BLUE].reverse() : [...SEQUENTIAL_BLUE],
    diverging: mode === "dark" ? { ...DIVERGING_DARK } : { ...DIVERGING_LIGHT },
    fontFamily: styles?.fontFamily || SANS,
  };
}

/**
 * Series key → colour, assigned in the order the spec declares them.
 *
 * Lookup is by key and the map is built from the chart's full declared set, so
 * dropping a series from what is drawn cannot repaint the ones that remain —
 * the colour follows the entity, never its position among the visible traces.
 */
export function seriesColors(theme: ChartTheme, keys: string[]): Record<string, string> {
  const colors: Record<string, string> = {};
  keys.forEach((key, index) => {
    // Past the eighth slot a generated hue is indistinguishable from one
    // already in use, so the tail goes grey — the "Other" fold, in colour.
    colors[key] = theme.series[index] ?? theme.muted;
  });
  return colors;
}

/** The chrome every chart shares: recessive grid, ink text, no surface of its own. */
export function baseLayout(theme: ChartTheme): Partial<Layout> {
  const axis = {
    gridcolor: theme.gridline,
    gridwidth: 1,
    zerolinecolor: theme.gridline,
    zerolinewidth: 1,
    linecolor: theme.gridline,
    tickcolor: theme.gridline,
    tickfont: { size: 11, color: theme.textSecondary },
    automargin: true,
  } as const;

  return {
    // The card owns the surface. Painting it here too would mean the palette
    // was validated against a colour the chart no longer sits on.
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: theme.fontFamily, size: 12, color: theme.textSecondary },
    margin: { l: 56, r: 20, t: 8, b: 40 },
    showlegend: false,
    legend: {
      orientation: "h",
      yanchor: "bottom",
      y: 1.02,
      x: 0,
      font: { size: 11, color: theme.textSecondary },
    },
    hoverlabel: {
      bgcolor: theme.surface,
      bordercolor: theme.gridline,
      font: { family: theme.fontFamily, size: 12, color: theme.textPrimary },
      align: "left",
    },
    // Plotly's default modebar is a dark slab that lands on top of the legend.
    // Chrome recedes: transparent, in the same muted ink as the axes.
    modebar: {
      bgcolor: "rgba(0,0,0,0)",
      color: theme.textSecondary,
      activecolor: theme.textPrimary,
      orientation: "h",
    },
    xaxis: { ...axis },
    yaxis: { ...axis },
  };
}

/**
 * Plotly's chrome. The modebar stays — zooming into a period is real analysis
 * work — but trimmed to the cartesian tools and without the logo.
 */
export const PLOT_CONFIG: Partial<Config> = {
  displaylogo: false,
  responsive: true,
  modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
  toImageButtonOptions: { format: "png", scale: 2 },
};
