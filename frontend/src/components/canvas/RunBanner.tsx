import { AlertTriangle, RefreshCw } from "lucide-react";
import { useState } from "react";

import type { RerunReport, RunDetail } from "../../lib/types";
import { riskFlags } from "./artifacts";

const STATUS_LABEL: Record<string, string> = {
  running: "Running",
  completed: "Completed",
  blocked: "Blocked",
  failed: "Failed",
};

/**
 * What this run was, how it went, and what is wrong with the data under it.
 *
 * The risk flags live here rather than in a tab because they qualify every
 * artifact below them at once. `synthetic_data` is the one that matters: a
 * canvas rendering generated prices as though they were market data undoes the
 * honesty the Data Steward was built for, so it is an `alert` and it stays on
 * screen whichever tab is open.
 */
export function RunBanner({
  run,
  onRerun,
}: {
  run: RunDetail;
  onRerun?: (runId: string) => Promise<RerunReport>;
}) {
  const [report, setReport] = useState<RerunReport | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [failure, setFailure] = useState("");

  const flags = riskFlags(run.outcome);

  const rerun = async (): Promise<void> => {
    if (!onRerun) return;
    setRerunning(true);
    setFailure("");
    try {
      setReport(await onRerun(run.id));
    } catch (error) {
      setFailure(error instanceof Error ? error.message : String(error));
    } finally {
      setRerunning(false);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{run.question}</h2>
          <p className="mt-0.5 text-2xs text-text-secondary">
            {STATUS_LABEL[run.status] ?? run.status} · {run.tier} tier ·{" "}
            {run.input_tokens.toLocaleString()} in / {run.output_tokens.toLocaleString()} out ·{" "}
            {(run.latency_ms / 1000).toFixed(1)}s
            {run.outcome.quality ? ` · ${run.outcome.quality.rows} rows` : ""}
          </p>
        </div>

        {onRerun && (
          <button
            type="button"
            onClick={() => void rerun()}
            disabled={rerunning}
            className="flex shrink-0 items-center gap-1.5 rounded border border-border px-2 py-1 text-2xs text-text-secondary hover:text-text-primary disabled:opacity-60"
          >
            <RefreshCw aria-hidden className={`size-3 ${rerunning ? "animate-spin" : ""}`} />
            Re-run
          </button>
        )}
      </div>

      {flags.length > 0 && (
        <div
          role="alert"
          className="rounded-md border border-negative/40 bg-negative/8 px-3 py-2 text-2xs"
        >
          {flags.map((flag) => (
            <p key={flag.code} className="flex gap-2">
              <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-negative" />
              <span>
                <span className="font-medium">{flag.code.replace(/_/g, " ")}</span> — {flag.detail}
              </span>
            </p>
          ))}
        </div>
      )}

      {run.outcome.warnings?.map((warning) => (
        <p key={warning} className="text-2xs text-text-secondary">
          {warning}
        </p>
      ))}

      {(report || failure) && (
        <div
          role="status"
          className="rounded-md border border-border bg-surface-1 px-3 py-2 text-2xs"
        >
          {failure ? (
            <span>The re-run could not be completed — {failure}</span>
          ) : report?.reproduced ? (
            <span>
              Re-run <span className="font-medium text-positive">reproduced</span> this result:
              every step returned the same numbers from the same data.
            </span>
          ) : (
            <div className="space-y-1">
              <span className="font-medium text-negative">This result did not reproduce.</span>
              {report?.steps
                .filter((step) => !step.reproduced)
                .map((step) => (
                  <p key={step.step_id}>
                    <span className="font-medium">{step.step_id}</span> ({step.tool}) —{" "}
                    {step.detail}
                  </p>
                ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
