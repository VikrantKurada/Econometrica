import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunOutcome } from "../../lib/types";
import { Provenance, stepProvenance } from "./Provenance";
import { FIXTURE_RUN } from "./fixtures";

/**
 * The manifest, on paper.
 *
 * An exported artifact that cannot be traced back is exactly what this project
 * exists not to produce, so a printed report carries the same provenance the
 * ZIP export puts in `manifest.json`. On screen the canvas shows the data
 * banner and the trace; on paper neither is reachable.
 */

const OUTCOME = FIXTURE_RUN.outcome as Partial<RunOutcome>;

describe("stepProvenance", () => {
  it("reads a manifest from every step that produced a result", () => {
    const steps = stepProvenance(OUTCOME);

    expect(steps.length).toBeGreaterThan(0);
    expect(steps[0].tool).toBeTruthy();
    expect(steps[0].fingerprint).toBeTruthy();
  });

  it("skips a step that produced nothing", () => {
    // A refused step has no manifest, and blank cells read like lost
    // provenance rather than like a step that produced nothing — the same
    // lesson the ZIP export learned.
    const refusedOnly: Partial<RunOutcome> = {
      execution: {
        ...OUTCOME.execution!,
        outcomes: OUTCOME.execution!.outcomes.map((step) => ({
          ...step,
          result: null,
        })),
      },
    };

    expect(stepProvenance(refusedOnly)).toEqual([]);
  });

  it("handles an outcome with no execution at all", () => {
    expect(stepProvenance({})).toEqual([]);
  });
});

describe("Provenance", () => {
  it("is print-only by default", () => {
    render(<Provenance outcome={OUTCOME} />);

    expect(screen.getByLabelText("Provenance").className).toContain("print-only");
  });

  it("can be shown on screen when asked", () => {
    render(<Provenance outcome={OUTCOME} visible />);

    expect(screen.getByLabelText("Provenance").className).not.toContain("print-only");
  });

  it("names the question the report answers", () => {
    render(<Provenance outcome={OUTCOME} visible />);

    expect(screen.getByText(/Question:/)).toBeTruthy();
  });

  it("carries the data source, window and fingerprint", () => {
    render(<Provenance outcome={OUTCOME} visible />);

    const text = screen.getByLabelText("Provenance").textContent ?? "";
    expect(text).toContain(OUTCOME.quality!.source);
    expect(text).toContain(OUTCOME.quality!.fingerprint);
  });

  it("carries every result's tool, version and data fingerprint", () => {
    render(<Provenance outcome={OUTCOME} visible />);

    const table = screen.getByLabelText("Result manifests");
    const first = stepProvenance(OUTCOME)[0];

    expect(within(table).getAllByText(first.fingerprint).length).toBeGreaterThan(0);
    expect(within(table).getAllByText(first.tool).length).toBeGreaterThan(0);
  });

  it("shows a fingerprint in full rather than truncated", () => {
    // A fingerprint you cannot compare in full is not a fingerprint.
    render(<Provenance outcome={OUTCOME} visible />);
    const first = stepProvenance(OUTCOME)[0];

    // `getAllBy`: steps of one run share a data fingerprint, because they ran
    // on the same frame. That they agree is the point of recording it.
    const cells = screen.getAllByText(first.fingerprint);

    expect(cells[0].textContent).toBe(first.fingerprint);
    expect(cells[0].textContent).not.toContain("…");
  });

  it("carries the data-quality flags, including the synthetic warning", () => {
    render(<Provenance outcome={OUTCOME} visible />);

    const text = screen.getByLabelText("Provenance").textContent ?? "";
    for (const flag of OUTCOME.quality!.flags) {
      expect(text).toContain(flag.code);
    }
  });

  it("says so when a run produced nothing to reproduce", () => {
    render(<Provenance outcome={{}} visible />);

    expect(screen.getByText(/nothing to reproduce/i)).toBeTruthy();
  });
});
