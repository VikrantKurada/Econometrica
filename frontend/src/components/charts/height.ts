import { createContext } from "react";

/**
 * A height override for the box a chart happens to be drawn in.
 *
 * Each chart type picks a sensible height for itself — a forest plot grows
 * with its coefficients, a two-panel stack is taller than a bar chart — and
 * that is right for the canvas. Full screen is the exception: there the
 * container, not the chart type, should decide, and Plotly's `responsive`
 * config only reflows width.
 *
 * Context rather than a prop threaded through all fourteen components: the
 * height is a fact about the surrounding box, and every intermediate component
 * would otherwise carry a prop it has no opinion about.
 */
export const ChartHeight = createContext<number | null>(null);
