import type { RunOutcome } from "../../lib/types";

/**
 * The interpretation, or an account of why there isn't one.
 *
 * When the grounding gate withholds a draft it withholds the *whole* draft —
 * unmatched figures are never edited out — so the honest thing to render is
 * the reason, naming the numbers that matched nothing computed. A blank panel
 * would read as "the analysis found nothing to say".
 */
export function Narrative({ outcome }: { outcome: Partial<RunOutcome> }) {
  const narration = outcome.narration;

  if (!narration) {
    return (
      <p className="px-1 py-6 text-center text-2xs text-text-secondary">
        This run produced no interpretation.
      </p>
    );
  }

  if (!narration.published) {
    return (
      <div className="space-y-2 px-1 py-2 text-xs">
        <p className="font-medium text-negative">
          The interpretation was withheld because it cited numbers no result supports.
        </p>
        <p className="text-2xs text-text-secondary">
          {narration.grounding.checked} number(s) were checked against the computed results. The
          whole draft is held back rather than edited, so nothing survives that was not traceable.
        </p>
        <ul className="space-y-1">
          {narration.grounding.issues.map((issue, index) => (
            <li key={index} className="rounded border border-border bg-surface-2 px-2 py-1">
              <span className="font-medium tabular">{issue.text}</span>
              <span className="text-text-secondary"> in “{issue.sentence.trim()}”</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  return (
    <div className="space-y-2 px-1 py-2">
      <p className="text-xs leading-relaxed">{narration.narrative?.prose}</p>
      {narration.narrative?.citations.length ? (
        <p className="text-2xs text-text-secondary">
          Drawn from {narration.narrative.citations.join(", ")}. Every number above matches a
          computed result.
        </p>
      ) : null}
    </div>
  );
}
