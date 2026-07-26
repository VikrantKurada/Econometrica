import type { RunStep } from "../../lib/types";

const STATUS_TINT: Record<string, string> = {
  ok: "text-text-secondary",
  refused: "text-negative",
  failed: "text-negative",
  skipped: "text-text-secondary",
};

/**
 * Every model call and tool invocation, in the order they happened.
 *
 * Rejected attempts are rows here too — they were billed, and a trace that
 * dropped them would understate exactly the runs where a safeguard did its
 * job. Ordered by the server's `seq`, never by `created_at`: the whole run is
 * written in one transaction, so every row carries the same timestamp.
 */
export function TraceTable({ steps }: { steps: RunStep[] }) {
  if (steps.length === 0) {
    return (
      <p className="px-1 py-6 text-center text-2xs text-text-secondary">
        This run left no trace.
      </p>
    );
  }

  return (
    <div className="scroll-thin overflow-auto">
      <table className="w-full border-collapse text-2xs" aria-label="Run trace">
        <thead>
          <tr className="text-text-secondary">
            {["", "agent", "what", "status", "tokens", "time"].map((column) => (
              <th
                key={column}
                scope="col"
                className="border-b border-border px-2 py-1.5 text-left font-medium"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...steps]
            .sort((a, b) => a.seq - b.seq)
            .map((step) => (
              <tr key={step.id}>
                <td className="tabular border-b border-border/60 px-2 py-1 text-text-secondary">
                  {step.seq}
                </td>
                <td className="border-b border-border/60 px-2 py-1">{step.agent}</td>
                <td className="border-b border-border/60 px-2 py-1 text-text-secondary">
                  {step.tool ?? step.model ?? step.kind}
                  {step.attempt > 1 ? ` (attempt ${step.attempt})` : ""}
                  {step.detail ? ` — ${step.detail}` : ""}
                </td>
                <td
                  className={`border-b border-border/60 px-2 py-1 ${
                    STATUS_TINT[step.status] ?? ""
                  }`}
                >
                  {step.status}
                </td>
                <td className="tabular border-b border-border/60 px-2 py-1 text-right">
                  {step.input_tokens + step.output_tokens || ""}
                </td>
                <td className="tabular border-b border-border/60 px-2 py-1 text-right">
                  {step.latency_ms >= 1000
                    ? `${(step.latency_ms / 1000).toFixed(1)}s`
                    : `${Math.round(step.latency_ms)}ms`}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
