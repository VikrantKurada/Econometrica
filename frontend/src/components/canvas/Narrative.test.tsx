import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Narration, RunOutcome } from "../../lib/types";
import { Narrative } from "./Narrative";

/**
 * A withheld interpretation has to say the *right* thing about why.
 *
 * There are two reasons a draft never reaches the reader and only one of them
 * is the grounding gate. Reporting the other as "cited numbers no result
 * supports" tells the user their model invented a statistic when in fact it
 * returned prose where JSON was asked for — and the same conflation is what
 * made the Phase 4 e2e gate pass or fail on the model's mood.
 */

function outcome(narration: Narration): Partial<RunOutcome> {
  return { status: "blocked", narration };
}

const GROUNDED = { grounded: true, issues: [], checked: 0 };

describe("a withheld narration", () => {
  it("names the invented figures when the gate withheld it", () => {
    render(
      <Narrative
        outcome={outcome({
          published: false,
          narrative: null,
          withheld_reason: "ungrounded",
          grounding: {
            grounded: false,
            checked: 14,
            issues: [{ value: -15.066, text: "-15.066", sentence: "The statistic is -15.066." }],
          },
        })}
      />,
    );

    expect(screen.getByText(/cited numbers no result supports/i)).toBeVisible();
    expect(screen.getByText("-15.066")).toBeVisible();
  });

  it("does not claim invented figures when no draft ever reached the gate", () => {
    render(
      <Narrative
        outcome={outcome({
          published: false,
          narrative: null,
          withheld_reason: "unusable_draft",
          grounding: GROUNDED,
        })}
      />,
    );

    expect(screen.queryByText(/cited numbers no result supports/i)).toBeNull();
    expect(screen.getByText(/no draft could be used/i)).toBeVisible();
  });

  it("renders the prose when it was published", () => {
    render(
      <Narrative
        outcome={outcome({
          published: true,
          narrative: { prose: "The series wanders.", citations: ["s1"] },
          withheld_reason: "",
          grounding: GROUNDED,
        })}
      />,
    );

    expect(screen.getByText("The series wanders.")).toBeVisible();
  });
});
