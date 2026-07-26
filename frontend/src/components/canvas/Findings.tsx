import { CircleSlash, HelpCircle } from "lucide-react";

import type { RunOutcome } from "../../lib/types";
import { refusals, unjudged } from "./artifacts";

/**
 * What the analysis declined to do, and what it could not check.
 *
 * A canvas that showed only the steps that ran would misrepresent the run: a
 * refused GARCH is the system deciding not to produce a number that would be
 * taken seriously, which is the most valuable thing it does. And an unjudged
 * check is kept visibly apart from a refusal — "nobody could test this" is not
 * "this failed", the same tri-state `Diagnostic.passed` keeps.
 */
export function Findings({ outcome }: { outcome: Partial<RunOutcome> }) {
  const refused = refusals(outcome);
  const unchecked = unjudged(outcome);
  if (refused.length === 0 && unchecked.length === 0) return null;

  return (
    <section
      aria-label="Findings"
      className="rounded-md border border-border bg-surface-1 px-3 py-2"
    >
      <h3 className="mb-1.5 text-2xs font-semibold uppercase tracking-wide text-text-secondary">
        Findings
      </h3>
      <ul className="space-y-1.5">
        {refused.map((verdict, index) => (
          <li key={`refused-${index}`} className="flex gap-2 text-2xs">
            <CircleSlash aria-hidden className="mt-0.5 size-3.5 shrink-0 text-negative" />
            <span>
              <span className="font-medium">{verdict.tool}</span>{" "}
              <span className="text-text-secondary">({verdict.step_id})</span> refused —{" "}
              {verdict.detail}
            </span>
          </li>
        ))}
        {unchecked.map((verdict, index) => (
          <li key={`unjudged-${index}`} className="flex gap-2 text-2xs">
            <HelpCircle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-text-secondary" />
            <span>
              <span className="font-medium">{verdict.tool}</span>{" "}
              <span className="text-text-secondary">({verdict.step_id})</span>{" "}
              <em className="not-italic text-text-secondary">not judged</em> — {verdict.detail}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
