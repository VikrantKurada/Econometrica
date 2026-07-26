import type { Diagnostic, RunOutcome } from "../../lib/types";

/** Tri-state, and the third state is not a soft "no". */
function verdict(passed: boolean | null): { label: string; tint: string } {
  if (passed === null) return { label: "not judged", tint: "text-text-secondary" };
  return passed ? { label: "passed", tint: "text-positive" } : { label: "failed", tint: "text-negative" };
}

function number(value: number | null, digits = 4): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return String(Number.parseFloat(value.toPrecision(digits)));
}

/**
 * The assumption checks, and what they found.
 *
 * This panel exists because a whole class of result has no chart. A pure
 * hypothesis test — `adf`, `kpss` — reports a statistic and a p-value, and
 * neither is a series, a table, an estimate or a scalar, so no member of the
 * chart union can bind to one. `charts/propose.py` proposes nothing for them
 * on purpose rather than falling back to a tile of the sample size; this is
 * where their finding shows up instead.
 *
 * `passed` is rendered in three states, never two. A check the tool could not
 * evaluate is not a check that failed, and collapsing them would report an
 * assumption as violated when nobody ever tested it.
 */
export function Diagnostics({ outcome }: { outcome: Partial<RunOutcome> }) {
  const diagnostics: Diagnostic[] = outcome.diagnostics ?? [];

  if (diagnostics.length === 0) {
    return (
      <p className="px-1 py-6 text-center text-2xs text-text-secondary">
        No assumption checks were run for this analysis.
      </p>
    );
  }

  return (
    <div className="scroll-thin overflow-auto">
      <table className="w-full border-collapse text-2xs" aria-label="Diagnostics">
        <thead>
          <tr className="text-text-secondary">
            {["check", "statistic", "p value", "verdict", "what it means"].map((column) => (
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
          {diagnostics.map((diagnostic, index) => {
            const { label, tint } = verdict(diagnostic.passed);
            return (
              <tr key={`${diagnostic.name}-${index}`}>
                <td className="border-b border-border/60 px-2 py-1">
                  {diagnostic.name.replace(/_/g, " ")}
                </td>
                <td className="tabular border-b border-border/60 px-2 py-1 text-right">
                  {number(diagnostic.statistic)}
                </td>
                <td className="tabular border-b border-border/60 px-2 py-1 text-right">
                  {number(diagnostic.p_value, 2)}
                </td>
                <td className={`border-b border-border/60 px-2 py-1 ${tint}`}>{label}</td>
                <td className="border-b border-border/60 px-2 py-1 text-text-secondary">
                  {diagnostic.interpretation}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
