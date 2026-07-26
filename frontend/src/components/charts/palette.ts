/**
 * The categorical series palette.
 *
 * These eight hexes are the `dataviz` skill's reference palette, re-validated
 * against *this project's* chart surfaces — `--surface-1`, which is `#fafafa`
 * light and `#121416` dark. Contrast only means anything against the surface
 * actually rendered on, which is why the chart card is `bg-surface-1` and not
 * the pane's `surface-0`: changing that invalidates the run below.
 *
 * ```
 * light  adjacent (8)   worst CVD ΔE 9.1 · normal-vision 19.6 · WARN contrast
 *                       aqua 2.7, yellow 2.07, magenta 2.58 below 3:1
 * light  all-pairs (3)  worst CVD ΔE 9.2 · normal-vision 24.0
 * dark   adjacent (8)   worst CVD ΔE 8.4 · normal-vision 19.3 · all 8 ≥ 3:1
 * dark   all-pairs (3)  worst CVD ΔE 9.4 · normal-vision 20.9
 * ```
 *
 * Three consequences are requirements, not preferences:
 *
 * - the light WARN triggers the relief rule, so every chart ships a table view
 *   and ≤ 4 series are direct-labelled. It is not dismissable.
 * - forms that compare every pair at once (scatter) cap at three series. The
 *   backend spec enforces it; see `SCATTER_SERIES_CAP` in `charts/spec.py`.
 * - the order is the colour-blindness mechanism, not decoration. Slots are
 *   assigned in sequence and never cycled; a ninth series folds to "Other".
 *
 * Duplicating the hexes here rather than only in CSS is deliberate: charts must
 * still get colours where no stylesheet is loaded (jsdom, and the standalone
 * export in task 5.5). `palette.test.ts` asserts the two copies agree.
 */

export const SERIES_LIGHT = [
  "#2a78d6", // 1 blue
  "#eb6834", // 2 orange
  "#1baf7a", // 3 aqua
  "#eda100", // 4 yellow
  "#e87ba4", // 5 magenta
  "#008300", // 6 green
  "#4a3aa7", // 7 violet
  "#e34948", // 8 red
] as const;

export const SERIES_DARK = [
  "#3987e5",
  "#d95926",
  "#199e70",
  "#c98500",
  "#d55181",
  "#008300", // the one slot that needed no re-stepping for the dark surface
  "#9085e9",
  "#e66767",
] as const;

/**
 * Blue, light→dark, for magnitude with no meaningful middle — a cointegration
 * p-value grid. Dark mode flips the anchor so "near zero" still recedes toward
 * the surface rather than glowing.
 */
export const SEQUENTIAL_BLUE = [
  "#cde2fb",
  "#b7d3f6",
  "#9ec5f4",
  "#86b6ef",
  "#6da7ec",
  "#5598e7",
  "#3987e5",
  "#2a78d6",
  "#256abf",
  "#1c5cab",
  "#184f95",
  "#104281",
  "#0d366b",
] as const;

/**
 * Blue ↔ red through a neutral gray, for polarity — a correlation matrix. The
 * midpoint must read as "nothing", which is why it is gray and not a hue, and
 * the poles must read as opposite, which is why they are warm/cool.
 */
export const DIVERGING_LIGHT = { low: "#e34948", mid: "#f0efec", high: "#2a78d6" } as const;
export const DIVERGING_DARK = { low: "#e66767", mid: "#383835", high: "#3987e5" } as const;
