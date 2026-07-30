/**
 * What a run produced, in the shapes the canvas draws.
 *
 * Three of these exist because the backend's own conveniences are Python
 * properties and never cross the wire: `ExecutionReport.results`,
 * `ExecutionReport.refusals` and `PreconditionVerdict.refused` are all
 * computed there and absent here. The rules are restated once, in this file,
 * rather than in each component that needs them.
 */

import type {
  PreconditionVerdict,
  QualityFlag,
  ResultSet,
  RunOutcome,
  StepOutcome,
} from "../../lib/types";
import type { ChartSpec } from "../charts/spec";

/** A chart and the numbers under it. */
export interface ChartArtifact {
  /** Stable across renders, so a tab keeps its place while a run streams. */
  id: string;
  spec: ChartSpec;
  result: ResultSet;
}

/** A gate's verdict, and which step it was about. */
export interface StepVerdict extends PreconditionVerdict {
  step_id: string;
}

function steps(outcome: Partial<RunOutcome>): StepOutcome[] {
  return outcome.execution?.outcomes ?? [];
}

function results(outcome: Partial<RunOutcome>): Map<string, ResultSet> {
  return new Map(
    steps(outcome)
      .filter((step) => step.result !== null)
      .map((step) => [step.step_id, step.result as ResultSet]),
  );
}

export function chartArtifacts(outcome: Partial<RunOutcome>): ChartArtifact[] {
  const byStep = results(outcome);

  return (outcome.charts ?? []).flatMap((spec, index) => {
    const result = byStep.get(spec.step_id);
    // A chart whose step produced nothing cannot be drawn from anything. The
    // backend does not propose one, so this means the outcome disagrees with
    // itself — and an unbound chart is worse than an absent one.
    if (!result) return [];
    return [{ id: `${spec.step_id}-${index}-${spec.type}`, spec, result }];
  });
}

/**
 * Checks that ran and said no.
 *
 * A refused step is a finding, not an absence: it is the system declining to
 * produce a number that would be taken seriously and should not be.
 */
export function refusals(outcome: Partial<RunOutcome>): StepVerdict[] {
  return verdicts(outcome).filter((verdict) => verdict.judged && !verdict.allowed);
}

/**
 * Checks that could not be evaluated at all.
 *
 * Kept apart from refusals for the reason `Diagnostic.passed` is tri-state:
 * "not judged" is not "failed", and a canvas that merged them would report an
 * assumption as violated when nobody ever tested it.
 */
export function unjudged(outcome: Partial<RunOutcome>): StepVerdict[] {
  return verdicts(outcome).filter((verdict) => !verdict.judged);
}

function verdicts(outcome: Partial<RunOutcome>): StepVerdict[] {
  return steps(outcome).flatMap((step) =>
    step.verdicts.map((verdict) => ({ ...verdict, step_id: step.step_id })),
  );
}

/**
 * Data quality flags a reader must not miss — `synthetic_data` above all.
 *
 * Rendering generated prices as though they were market data undoes the whole
 * point of the Data Steward, so these are shown beside the charts rather than
 * behind a tab.
 */
export function riskFlags(outcome: Partial<RunOutcome>): QualityFlag[] {
  return (outcome.quality?.flags ?? []).filter(
    (flag) => flag.severity === "risk" || flag.severity === "warning",
  );
}

/**
 * Flags that qualify the data without being wrong with it.
 *
 * `mixed_sources` is why this exists: a run that read a series from an upload
 * and another from a market source has to say which came from where, and the
 * banner showed risk and warning only — so the disclosure the backend raises
 * reached nobody. It is not a warning, because mixing an upload with fetched
 * tickers is the feature rather than a defect, so it renders as a note instead
 * of an alert. Together with `riskFlags` this partitions the flags: nothing is
 * shown twice and nothing is dropped.
 */
export function infoFlags(outcome: Partial<RunOutcome>): QualityFlag[] {
  return (outcome.quality?.flags ?? []).filter((flag) => flag.severity === "info");
}

/**
 * Every prefix a result's tool name can carry that means "no tested function
 * produced this". One entry, and it mirrors `SANDBOX_TOOL_PREFIX` in
 * `agents/quant_coder.py`. A colon cannot appear in a registry tool name, so
 * nothing in `econ/` can collide with it.
 */
const SANDBOX_TOOL_PREFIX = "sandbox:";

export interface UnvalidatedMethod {
  stepId: string;
  tool: string;
  /** What the model called it, or the tool name when it did not say. */
  method: string;
}

/**
 * Results computed by code a model wrote, rather than by a registry tool.
 *
 * Derived from the results themselves rather than from a flag carried
 * alongside them — the same reasoning as `synthetic_data` being read off the
 * source label. A marker that travels separately is a marker that can be lost,
 * and this is the one the whole escape hatch depends on: a sandbox number that
 * looked like a `capm` beta would undo the point of having a registry at all.
 */
export function unvalidatedMethods(outcome: Partial<RunOutcome>): UnvalidatedMethod[] {
  return steps(outcome).flatMap((step) => {
    const result = step.result;
    if (!result || !result.tool.startsWith(SANDBOX_TOOL_PREFIX)) return [];
    const method = result.params?.method;
    return [
      {
        stepId: step.step_id,
        tool: result.tool,
        method: typeof method === "string" && method ? method : result.tool,
      },
    ];
  });
}
