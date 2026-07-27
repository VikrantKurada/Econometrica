import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ResultSet, RunDetail, RunOutcome, StepOutcome } from "../../lib/types";
import { Provenance } from "./Provenance";
import { FIXTURE_RUN } from "./fixtures";
import { RunBanner } from "./RunBanner";

/**
 * How a run says a number was computed by code a model wrote.
 *
 * This is the surface the whole escape hatch turns on. Every restriction in
 * `sandbox/` only keeps the process from taking the machine with it; the thing
 * that keeps the project honest is that a result with no tested function
 * behind it never looks like one that has.
 */

const SANDBOX_RESULT: ResultSet = {
  tool: "sandbox:rolling_hurst",
  version: "unvalidated",
  params: { method: "Rolling Hurst exponent", code: "result = {}" },
  estimates: [],
  diagnostics: [],
  scalars: { hurst: 0.61 },
  tables: {},
  series: {},
  manifest: {
    data_fingerprint: "c".repeat(64),
    tool: "sandbox:rolling_hurst",
    tool_version: "unvalidated",
    params_hash: "d".repeat(64),
    library_versions: { numpy: "2.5.1" },
    seed: null,
    created_at: "2026-07-27T00:00:00Z",
  },
};

const SANDBOX_STEP: StepOutcome = {
  step_id: "c1",
  tool: "sandbox:rolling_hurst",
  status: "ran",
  result: SANDBOX_RESULT,
  verdicts: [],
  error: "",
};

function withSandboxStep(): RunDetail {
  const outcome: Partial<RunOutcome> = {
    ...FIXTURE_RUN.outcome,
    execution: {
      outcomes: [...FIXTURE_RUN.outcome.execution!.outcomes, SANDBOX_STEP],
    },
  };
  return { ...FIXTURE_RUN, outcome };
}

describe("the unvalidated-method notice", () => {
  it("is shown in the banner when a run used generated code", () => {
    render(<RunBanner run={withSandboxStep()} />);

    const notice = screen.getByRole("alert", { name: /unvalidated method/i });
    expect(notice).toHaveTextContent("Rolling Hurst exponent");
    expect(notice).toHaveTextContent("c1");
  });

  it("is absent from an ordinary run", () => {
    // The false positive matters as much as the false negative: labelling a
    // CAPM unvalidated would train a reader to ignore the notice.
    render(<RunBanner run={FIXTURE_RUN} />);

    expect(screen.queryByRole("alert", { name: /unvalidated method/i })).toBeNull();
  });

  it("survives into the printed provenance", () => {
    // On paper the banner is gone and the trace is unreachable, so the one
    // place a reader could learn this has to carry it too.
    render(<Provenance outcome={withSandboxStep().outcome} visible />);

    const section = screen.getByRole("region", { name: "Provenance" });
    expect(section).toHaveTextContent(/unvalidated method/i);
    expect(section).toHaveTextContent("Rolling Hurst exponent");
  });
});
