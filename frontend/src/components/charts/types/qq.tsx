import type { ResultSet } from "../../../lib/types";
import {
  axisTitles,
  LINE_WIDTH,
  MARKER_SIZE,
  mergeLayout,
  perMark,
  type Figure,
} from "../marks";
import { PlotlyFigure } from "../PlotlyFigure";
import type { QqChartSpec } from "../spec";
import { baseLayout, type ChartTheme } from "../theme";

/**
 * The inverse normal CDF (Acklam's rational approximation, ~1e-9 relative).
 *
 * Placing sorted observations against the reference distribution's quantiles is
 * a coordinate transform of data the tool already emitted, in the same family
 * as a histogram's binning: it produces no estimate, nothing citable, and
 * nothing that reaches a manifest. It is not the tool boundary being crossed —
 * that would be computing a statistic here and reporting it as a finding.
 */
export function probit(p: number): number {
  const a = [-39.6968302866538, 220.946098424521, -275.928510446969, 138.357751867269,
    -30.6647980661472, 2.50662827745924];
  const b = [-54.4760987982241, 161.585836858041, -155.698979859887, 66.8013118877197,
    -13.2806815528857];
  const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184, -2.54973253934373,
    4.37466414146497, 2.93816398269878];
  const d = [0.00778469570904146, 0.32246712907004, 2.445134137143, 3.75440866190742];
  const low = 0.02425;

  if (p <= 0 || p >= 1) return p <= 0 ? Number.NEGATIVE_INFINITY : Number.POSITIVE_INFINITY;

  if (p < low || p > 1 - low) {
    const tail = p < low ? p : 1 - p;
    const q = Math.sqrt(-2 * Math.log(tail));
    const value =
      (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    return p < low ? value : -value;
  }

  const q = p - 0.5;
  const r = q * q;
  return (
    ((((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q) /
    (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
  );
}

export function buildQq(spec: QqChartSpec, result: ResultSet, theme: ChartTheme): Figure {
  const series = result.series[spec.series.key];
  const sorted = ((series?.y ?? []).filter((v) => v !== null) as number[]).sort((a, b) => a - b);
  const n = sorted.length;

  const theoretical = sorted.map((_, i) => probit((i + 0.5) / n));
  const sample = (p: number) => sorted[Math.min(n - 1, Math.max(0, Math.round(p * (n - 1))))];

  // The reference is the line through both distributions' quartiles — the
  // usual construction, and the one that does not assume the residuals were
  // standardized before they got here.
  const slope = (sample(0.75) - sample(0.25)) / (probit(0.75) - probit(0.25));
  const intercept = sample(0.25) - slope * probit(0.25);
  const ends = n ? [theoretical[0], theoretical[n - 1]] : [];

  return {
    data: [
      {
        type: "scatter" as const,
        mode: "lines" as const,
        name: "Reference",
        x: ends,
        y: ends.map((z) => intercept + slope * z),
        line: { color: theme.textSecondary, width: LINE_WIDTH, dash: "dot" as const },
        hoverinfo: "skip" as const,
      },
      {
        type: "scatter" as const,
        mode: "markers" as const,
        name: spec.series.label,
        x: theoretical,
        y: sorted,
        marker: {
          color: theme.series[0],
          size: MARKER_SIZE,
          opacity: 0.75,
          line: { width: 1.5, color: theme.surface },
        },
        hovertemplate: "theoretical %{x:.2f} · sample %{y:.4~g}<extra></extra>",
      },
    ],
    layout: mergeLayout(
      baseLayout(theme),
      perMark(),
      axisTitles(
        theme,
        spec.x_label || "Theoretical quantiles",
        spec.y_label || "Sample quantiles",
      ),
      { showlegend: false },
    ),
  };
}

export function QqChart({ spec, result }: { spec: QqChartSpec; result: ResultSet }) {
  return <PlotlyFigure spec={spec} result={result} build={buildQq} height={320} />;
}
