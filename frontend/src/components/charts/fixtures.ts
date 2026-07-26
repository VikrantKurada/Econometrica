/**
 * Fixture results and one spec of every type.
 *
 * Dev and test only — nothing in the shipped app imports this. It exists so the
 * renderers can be exercised before the canvas wires up real runs (task 5.4),
 * and so the gallery harness at `/gallery.html` can be screenshotted in both
 * themes, which is the last step of the chart procedure and the one a validator
 * cannot do for you.
 *
 * The numbers are generated, not measured. They are shaped like real tool
 * output — the same series names `charts/propose.py` keys on — so the specs
 * here are the specs the backend actually emits, but nothing in this file is
 * evidence about any market.
 */

import type { ResultSet, Series } from "../../lib/types";
import type { ChartSpec } from "./spec";

/** Deterministic, so a screenshot diff means a rendering change. */
function random(seed: number): () => number {
  let state = seed;
  return () => {
    state = (state + 0x6d2b79f5) | 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const DAYS = 260;
const dice = random(20260725);

const dates = Array.from({ length: DAYS }, (_, i) => {
  const day = new Date(Date.UTC(2025, 6, 1) + i * 86400000);
  return day.toISOString().slice(0, 10);
});

function ref(key: string, label = ""): { key: string; label: string } {
  return { key, label: label || key };
}

function series(
  name: string,
  y: (number | null)[],
  x: (string | number | null)[] = dates,
): Series {
  return { name, x, y };
}

// A return path with a volatility cluster in it, so the GARCH panel has
// something to show and the drawdown has a shape.
const returns: number[] = [];
let volatility = 0.008;
for (let i = 0; i < DAYS; i += 1) {
  const shock = (dice() + dice() + dice() - 1.5) * 1.4;
  volatility = Math.sqrt(0.000002 + 0.08 * (returns.at(-1) ?? 0) ** 2 + 0.9 * volatility ** 2);
  returns.push(shock * volatility);
}

const conditionalVolatility = returns.map((_, i) => {
  const window = returns.slice(Math.max(0, i - 20), i + 1);
  return Math.sqrt(window.reduce((sum, r) => sum + r * r, 0) / window.length) * Math.sqrt(252);
});

const cumulative = returns.reduce<number[]>((path, r) => {
  path.push((path.at(-1) ?? 1) * (1 + r));
  return path;
}, []);

const drawdown = cumulative.map((value, i) => value / Math.max(...cumulative.slice(0, i + 1)) - 1);

const beta = returns.map((_, i) => 1.05 + 0.35 * Math.sin(i / 34) + (dice() - 0.5) * 0.05);
const betaError = beta.map(() => 0.12 + dice() * 0.05);

const residuals = returns.map((r) => r / 0.011);

const lags = Array.from({ length: 21 }, (_, i) => i);
const acf = lags.map((lag) => (lag === 0 ? 1 : Math.exp(-lag / 4.5) * 0.42 * (dice() > 0.2 ? 1 : -1)));
const bartlett = lags.map((lag) => 1.96 / Math.sqrt(DAYS - lag));

/** One result standing in for a fitted model, carrying every series the specs use. */
export const FIXTURE_RESULT: ResultSet = {
  tool: "garch",
  version: "1.0.0",
  params: { p: 1, q: 1, dist: "t" },
  estimates: [
    { name: "alpha", value: 0.0004, std_error: 0.0002, t_stat: 1.82, p_value: 0.069, ci_low: -0.00003, ci_high: 0.0008 },
    { name: "mkt_rf", value: 1.08, std_error: 0.06, t_stat: 18.1, p_value: 0.0, ci_low: 0.96, ci_high: 1.2 },
    { name: "smb", value: -0.24, std_error: 0.09, t_stat: -2.67, p_value: 0.008, ci_low: -0.42, ci_high: -0.06 },
    { name: "hml", value: 0.11, std_error: 0.08, t_stat: 1.38, p_value: 0.169, ci_low: -0.05, ci_high: 0.27 },
    { name: "mom", value: -0.03, std_error: 0.07, t_stat: -0.43, p_value: 0.668, ci_low: -0.17, ci_high: 0.11 },
  ],
  diagnostics: [
    { name: "ljung_box", statistic: 14.2, p_value: 0.29, critical_values: {}, passed: true, interpretation: "no residual autocorrelation at lag 12" },
    { name: "jarque_bera", statistic: 88.4, p_value: 0.0, critical_values: {}, passed: false, interpretation: "residuals are not normal" },
    { name: "arch_lm", statistic: 3.1, p_value: 0.08, critical_values: {}, passed: null, interpretation: "" },
  ],
  scalars: {
    sharpe_ratio: 0.8412,
    max_drawdown: Math.min(...drawdown),
    annualized_vol: 0.1873,
    persistence: 0.9761,
  },
  tables: {
    correlation: {
      columns: ["", "SPY", "TLT", "GLD", "HYG"],
      rows: [
        ["SPY", 1, -0.38, 0.06, 0.72],
        ["TLT", -0.38, 1, 0.31, -0.19],
        ["GLD", 0.06, 0.31, 1, 0.11],
        ["HYG", 0.72, -0.19, 0.11, 1],
      ],
    },
    max_drawdown: {
      columns: ["start", "trough", "recovered", "depth"],
      rows: [
        ["2025-08-14", "2025-10-02", "2025-11-21", -0.1382],
        ["2026-01-09", "2026-02-11", "2026-03-30", -0.0917],
        ["2026-05-04", "2026-05-27", null, -0.0644],
      ],
    },
  },
  series: {
    realized_vol: series("realized_vol", conditionalVolatility.map((v) => v * 0.94)),
    ewma_vol: series("ewma_vol", conditionalVolatility),
    conditional_volatility: series("conditional_volatility", conditionalVolatility),
    standardized_residuals: series("standardized_residuals", residuals),
    residuals: series("residuals", residuals),
    beta: series("beta", beta),
    beta_ci_low: series("beta_ci_low", beta.map((b, i) => b - betaError[i])),
    beta_ci_high: series("beta_ci_high", beta.map((b, i) => b + betaError[i])),
    drawdown: series("drawdown", drawdown),
    regime_1_prob: series("regime_1_prob", returns.map((_, i) => 0.5 + 0.45 * Math.sin(i / 41))),
    regime_2_prob: series("regime_2_prob", returns.map((_, i) => 0.5 - 0.45 * Math.sin(i / 41))),
    acf: series("acf", acf, lags),
    acf_upper: series("acf_upper", bartlett, lags),
    acf_lower: series("acf_lower", bartlett.map((b) => -b), lags),
    vr_by_horizon: series("vr_by_horizon", [1.04, 0.97, 0.89, 0.83, 0.79, 0.74], [2, 4, 8, 16, 32, 64]),
    market_excess: series("market_excess", returns.map((r) => r * 0.8 + (dice() - 0.5) * 0.004)),
    asset_excess: series("asset_excess", returns),
  },
  manifest: {
    data_fingerprint: "sha256:9f2c…",
    tool: "garch",
    tool_version: "1.0.0",
    params_hash: "a41e…",
    library_versions: { arch: "7.2.0", numpy: "2.3.1" },
    seed: 20260725,
    created_at: "2026-07-26T09:00:00Z",
  },
};

function base(title: string, subtitle = ""): {
  title: string;
  subtitle: string;
  caption: string;
  step_id: string;
  x_label: string;
  y_label: string;
} {
  return { title, subtitle, caption: "", step_id: "step-1", x_label: "", y_label: "" };
}

/** One spec of every type in the union, in the order `spec.ts` declares them. */
export const GALLERY: ChartSpec[] = [
  {
    ...base("Volatility estimates", "Two estimators of the same quantity, one scale"),
    type: "line",
    series: [ref("realized_vol", "Realized"), ref("ewma_vol", "EWMA")],
    y_label: "Annualized",
  },
  {
    ...base("Rolling beta", "Shaded band is the confidence interval"),
    type: "band",
    center: ref("beta", "Beta"),
    lower: ref("beta_ci_low"),
    upper: ref("beta_ci_high"),
  },
  {
    ...base("Autocorrelation", "Bars outside the band are significant at the tested level"),
    type: "stem",
    series: ref("acf"),
    upper: ref("acf_upper"),
    lower: ref("acf_lower"),
    x_label: "Lag",
  },
  {
    ...base("GARCH fit", "Fitted volatility and the residuals it leaves behind"),
    type: "panels",
    panels: [
      { title: "Conditional volatility", series: [ref("conditional_volatility")], y_label: "" },
      { title: "Standardized residuals", series: [ref("standardized_residuals")], y_label: "" },
    ],
    shared_x: true,
  },
  {
    ...base("Security market line", "Asset excess return against the market's"),
    type: "scatter",
    x: ref("market_excess", "Market excess"),
    y: ref("asset_excess", "Asset excess"),
    groups: [],
    fit: true,
  },
  {
    ...base("Variance ratio by horizon", "A random walk sits at 1 across every horizon"),
    type: "bar",
    series: [ref("vr_by_horizon", "Variance ratio")],
    horizontal: false,
    x_label: "Horizon",
  },
  {
    ...base("Coefficients", "Intervals crossing zero are not distinguishable from no effect"),
    type: "forest",
    estimates: ["alpha", "mkt_rf", "smb", "hml", "mom"],
  },
  {
    ...base("Correlation matrix", "Diverging through a meaningful zero"),
    type: "heatmap",
    table: "correlation",
    scale: "diverging",
    domain: [-1, 1],
  },
  {
    ...base("Residual QQ plot", "Departure from the line is departure from normality"),
    type: "qq",
    series: ref("residuals"),
    reference: "normal",
  },
  {
    ...base("Residual distribution"),
    type: "histogram",
    series: ref("residuals"),
    bins: 40,
  },
  {
    ...base("Regime probabilities", "Two states, summing to one"),
    type: "area_stack",
    series: [ref("regime_1_prob", "Regime 1"), ref("regime_2_prob", "Regime 2")],
    y_label: "Probability",
  },
  {
    ...base("Drawdown", "Depth below the running maximum"),
    type: "underwater",
    series: ref("drawdown"),
  },
  {
    ...base("Sharpe ratio"),
    type: "stat_tile",
    scalar: "sharpe_ratio",
    unit: "",
    precision: 2,
  },
  {
    ...base("Drawdown episodes"),
    type: "table",
    table: "max_drawdown",
  },
];
