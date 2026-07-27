import type { Metrics } from "../../lib/types";

/**
 * What the workbench has spent, and where it has been slow.
 *
 * Two sources, kept visibly apart because they answer different questions and
 * because merging them is how a cost dashboard starts double-counting: tokens
 * and the pipeline's own rates come from the run steps, latencies from spans.
 *
 * A rate that has no denominator is rendered as **"not yet"**, never as 0%.
 * Zero would read as "nothing has ever failed", which is a claim about the
 * system; having run nothing is a statement about the sample.
 */

export interface CostDashboardProps {
  metrics: Metrics;
}

export function CostDashboard({ metrics }: CostDashboardProps) {
  const totalTokens =
    metrics.tokens.input +
    metrics.tokens.output +
    metrics.tokens.cache_read +
    metrics.tokens.cache_write;

  return (
    <section aria-label="Cost and latency" className="flex flex-col gap-4 p-1">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Tile label="Runs" value={metrics.runs.toLocaleString()} />
        <Tile label="Tokens" value={totalTokens.toLocaleString()} />
        <Tile label="Cost" value={`$${metrics.cost_usd.toFixed(4)}`} />
        <Tile
          label="Revisions"
          value={
            metrics.revisions_mean === null
              ? "not yet"
              : metrics.revisions_mean.toFixed(2)
          }
          hint="mean per run"
        />
        <Tile label="Tool errors" value={percent(metrics.tool_error_rate)} />
        <Tile
          label="Validator rejections"
          value={percent(metrics.validator_rejection_rate)}
        />
      </div>

      <Breakdown title="Spend by provider" rows={metrics.tokens_by_provider} />
      <Breakdown title="Spend by role" rows={metrics.tokens_by_agent} />

      <div className="flex flex-col gap-1">
        <h3 className="text-2xs font-medium text-text-primary">Latency</h3>
        {metrics.spans.length === 0 ? (
          <p className="text-2xs text-text-secondary">
            Nothing has been measured yet.
          </p>
        ) : (
          <table className="w-full text-left text-2xs" aria-label="Latency by operation">
            <thead className="text-text-secondary">
              <tr>
                {["operation", "n", "p50", "p95", "p99", "errors"].map((head) => (
                  <th key={head} scope="col" className="py-1 pr-3 font-medium">
                    {head}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {metrics.spans.map((entry) => (
                <tr key={entry.name} className="border-t border-border">
                  <td className="py-1 pr-3 font-mono text-text-primary">{entry.name}</td>
                  <td className="tabular py-1 pr-3 text-text-secondary">{entry.count}</td>
                  <td className="tabular py-1 pr-3 text-text-secondary">
                    {millis(entry.p50)}
                  </td>
                  <td className="tabular py-1 pr-3 text-text-secondary">
                    {millis(entry.p95)}
                  </td>
                  <td className="tabular py-1 pr-3 text-text-secondary">
                    {millis(entry.p99)}
                  </td>
                  <td
                    className={`tabular py-1 ${
                      entry.error_rate > 0 ? "text-negative" : "text-text-secondary"
                    }`}
                  >
                    {(entry.error_rate * 100).toFixed(0)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

function Breakdown({
  title,
  rows,
}: {
  title: string;
  rows: Metrics["tokens_by_provider"];
}) {
  if (rows.length === 0) return null;

  return (
    <div className="flex flex-col gap-1">
      <h3 className="text-2xs font-medium text-text-primary">{title}</h3>
      <table className="w-full text-left text-2xs">
        <tbody>
          {rows.map((row) => (
            <tr key={row.key} className="border-t border-border">
              <td className="py-1 pr-3 text-text-primary">{row.key}</td>
              <td className="tabular py-1 pr-3 text-text-secondary">
                {(row.input + row.output).toLocaleString()} tokens
              </td>
              <td className="tabular py-1 text-text-secondary">
                ${row.cost_usd.toFixed(4)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded border border-border bg-surface-1 p-2">
      <p className="text-2xs text-text-secondary">{label}</p>
      <p className="tabular text-sm text-text-primary">{value}</p>
      {hint && <p className="text-2xs text-text-secondary">{hint}</p>}
    </div>
  );
}

/** `null` means no denominator — see the module docstring. */
function percent(rate: number | null): string {
  return rate === null ? "not yet" : `${(rate * 100).toFixed(0)}%`;
}

function millis(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
}
