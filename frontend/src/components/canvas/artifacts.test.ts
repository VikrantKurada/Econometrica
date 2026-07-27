import { describe, expect, it } from "vitest";

import type { PreconditionVerdict, RunOutcome, StepOutcome } from "../../lib/types";
import { FIXTURE_RESULT } from "../charts/fixtures";
import {
  chartArtifacts,
  refusals,
  riskFlags,
  unjudged,
  unvalidatedMethods,
} from "./artifacts";

function verdict(overrides: Partial<PreconditionVerdict> = {}): PreconditionVerdict {
  return {
    tool: "garch",
    check: "arch_effects",
    allowed: true,
    judged: true,
    detail: "",
    ...overrides,
  };
}

function step(overrides: Partial<StepOutcome> = {}): StepOutcome {
  return {
    step_id: "s1",
    tool: "garch",
    status: "ran",
    result: FIXTURE_RESULT,
    verdicts: [],
    error: "",
    ...overrides,
  };
}

function outcome(overrides: Partial<RunOutcome> = {}): Partial<RunOutcome> {
  return {
    status: "completed",
    question: "Does it follow a random walk?",
    charts: [],
    execution: { outcomes: [step()] },
    ...overrides,
  };
}

describe("chartArtifacts", () => {
  it("pairs each chart with the result of the step that produced it", () => {
    // A chart names a step; the data lives on that step's ResultSet. Getting
    // this pairing wrong would draw one step's numbers under another's title.
    const artifacts = chartArtifacts(
      outcome({
        charts: [
          { ...blank("line"), step_id: "s1", series: [{ key: "realized_vol", label: "Vol" }] },
        ],
      }),
    );

    expect(artifacts).toHaveLength(1);
    expect(artifacts[0].result.tool).toBe("garch");
  });

  it("drops a chart whose step produced no result", () => {
    // Defensive: the backend only proposes charts for steps that ran, so this
    // means the outcome is inconsistent — and an unbound chart is worse than
    // an absent one.
    const artifacts = chartArtifacts(
      outcome({
        charts: [{ ...blank("line"), step_id: "ghost", series: [{ key: "x", label: "x" }] }],
        execution: { outcomes: [step({ status: "refused", result: null })] },
      }),
    );

    expect(artifacts).toEqual([]);
  });

  it("gives every artifact a stable id, so a tab keeps its place", () => {
    const charts = [
      { ...blank("line"), step_id: "s1", series: [{ key: "a", label: "a" }] },
      { ...blank("line"), step_id: "s1", series: [{ key: "b", label: "b" }] },
    ];
    const ids = chartArtifacts(outcome({ charts })).map((artifact) => artifact.id);

    expect(new Set(ids).size).toBe(2);
  });
});

describe("refusals and unjudged checks", () => {
  it("finds a refusal, which the wire does not mark as one", () => {
    // `PreconditionVerdict.refused` is a Python property, so it never reaches
    // the client. The rule — judged and not allowed — lives here instead.
    const refused = verdict({ allowed: false, judged: true, detail: "no ARCH effects" });
    const found = refusals(outcome({ execution: { outcomes: [step({ verdicts: [refused] })] } }));

    expect(found).toHaveLength(1);
    expect(found[0].detail).toBe("no ARCH effects");
  });

  it("keeps an unjudged check apart from a refusal", () => {
    // Tri-state, and the third state is the point: a check that could not be
    // evaluated is not a check that failed.
    const cannot = verdict({ allowed: false, judged: false, detail: "too few observations" });
    const report = outcome({ execution: { outcomes: [step({ verdicts: [cannot] })] } });

    expect(refusals(report)).toEqual([]);
    expect(unjudged(report)).toHaveLength(1);
  });

  it("says which step refused", () => {
    const refused = verdict({ allowed: false, judged: true });
    const found = refusals(
      outcome({ execution: { outcomes: [step({ step_id: "s7", verdicts: [refused] })] } }),
    );

    expect(found[0].step_id).toBe("s7");
  });
});

describe("riskFlags", () => {
  it("surfaces the synthetic_data flag", () => {
    // A canvas that renders generated prices as though they were market data
    // undoes the honesty the Data Steward was built for.
    const flags = riskFlags(
      outcome({
        quality: {
          tickers: ["AAA"],
          frequency: "D",
          return_method: "log",
          source: "synthetic",
          rows: 260,
          start: "2025-07-01",
          end: "2026-03-31",
          dropped_rows: 0,
          fingerprint: "sha256:…",
          flags: [
            { code: "synthetic_data", severity: "risk", detail: "prices are generated" },
            { code: "short_window", severity: "info", detail: "just under a year" },
          ],
        },
      }),
    );

    expect(flags.map((flag) => flag.code)).toEqual(["synthetic_data"]);
  });

  it("is empty when the data has nothing wrong with it", () => {
    expect(riskFlags(outcome())).toEqual([]);
  });
});

/** A chart spec with only the fields every member shares. */
function blank(type: "line") {
  return {
    type,
    title: "A chart",
    subtitle: "",
    caption: "",
    step_id: "",
    x_label: "",
    y_label: "",
  } as const;
}

describe("unvalidatedMethods", () => {
  const sandboxStep = {
    step_id: "c1",
    tool: "sandbox:rolling_hurst",
    status: "ran" as const,
    verdicts: [],
    error: "",
    result: {
      tool: "sandbox:rolling_hurst",
      version: "unvalidated",
      params: { method: "Rolling Hurst exponent", code: "result = {}" },
      estimates: [],
      diagnostics: [],
      scalars: { hurst: 0.61 },
      tables: {},
      series: {},
      manifest: {
        data_fingerprint: "a".repeat(64),
        tool: "sandbox:rolling_hurst",
        tool_version: "unvalidated",
        params_hash: "b".repeat(64),
        library_versions: {},
        seed: null,
        created_at: "2026-07-27T00:00:00Z",
      },
    },
  };

  it("names a step whose numbers came from generated code", () => {
    const found = unvalidatedMethods({ execution: { outcomes: [sandboxStep] } } as never);

    expect(found).toEqual([
      { stepId: "c1", tool: "sandbox:rolling_hurst", method: "Rolling Hurst exponent" },
    ]);
  });

  it("does not label an ordinary registry result", () => {
    // The property that must never regress: a CAPM marked unvalidated would
    // be as wrong as a sandbox result that was not.
    expect(unvalidatedMethods(outcome())).toEqual([]);
  });

  it("falls back to the tool name when the method was not recorded", () => {
    const step = { ...sandboxStep, result: { ...sandboxStep.result, params: {} } };

    const found = unvalidatedMethods({ execution: { outcomes: [step] } } as never);

    expect(found[0].method).toBe("sandbox:rolling_hurst");
  });

  it("ignores a step that produced no result", () => {
    const refused = { ...sandboxStep, status: "refused" as const, result: null };

    expect(unvalidatedMethods({ execution: { outcomes: [refused] } } as never)).toEqual([]);
  });
});
