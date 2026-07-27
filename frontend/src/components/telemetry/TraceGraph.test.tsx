import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { RunStep } from "../../lib/types";
import { TraceGraph, buildForest } from "./TraceGraph";

/**
 * What replaces the flat table: the parent links become structure, and a step
 * opens to what the model was asked and what it said.
 */

function step(overrides: Partial<RunStep> = {}): RunStep {
  return {
    id: "s1",
    seq: 1,
    parent_id: null,
    agent: "planner",
    kind: "llm",
    status: "ok",
    attempt: 1,
    provider: "ollama",
    model: "ministral-3:8b",
    input_tokens: 100,
    output_tokens: 20,
    cost_usd: 0.25,
    latency_ms: 1200,
    tool: null,
    tool_call_hash: null,
    detail: "",
    prompt: "",
    response: "",
    created_at: "2026-07-27T09:00:00Z",
    ...overrides,
  };
}

describe("buildForest", () => {
  it("nests a step under its parent", () => {
    const forest = buildForest([
      step({ id: "a", seq: 1 }),
      step({ id: "b", seq: 2, parent_id: "a" }),
    ]);

    expect(forest).toHaveLength(1);
    expect(forest[0].children[0].step.id).toBe("b");
  });

  it("keeps siblings in seq order, not insertion order", () => {
    const forest = buildForest([
      step({ id: "c", seq: 3, parent_id: "a" }),
      step({ id: "a", seq: 1 }),
      step({ id: "b", seq: 2, parent_id: "a" }),
    ]);

    expect(forest[0].children.map((c) => c.step.id)).toEqual(["b", "c"]);
  });

  it("shows a step whose parent is missing rather than dropping it", () => {
    // `parent_id` is ON DELETE SET NULL, so a hole in the trace is real —
    // and losing the work that came after it would be the worse failure.
    const forest = buildForest([step({ id: "b", seq: 2, parent_id: "gone" })]);

    expect(forest.map((n) => n.step.id)).toEqual(["b"]);
  });
});

describe("TraceGraph", () => {
  it("renders the parent link as structure, not just order", () => {
    render(
      <TraceGraph
        steps={[
          step({ id: "a", seq: 1, agent: "planner" }),
          step({ id: "b", seq: 2, parent_id: "a", agent: "narrator" }),
        ]}
      />,
    );

    const parent = screen.getByRole("button", { name: "planner" }).closest("li")!;
    // The child is inside the parent's list item — which is the edge.
    expect(within(parent).getByRole("button", { name: "narrator" })).toBeTruthy();
  });

  it("shows the provider and model for each step", () => {
    render(<TraceGraph steps={[step()]} />);

    expect(screen.getByText(/ollama · ministral-3:8b/)).toBeTruthy();
  });

  it("expands a step to what it was asked and what it said", async () => {
    render(
      <TraceGraph
        steps={[step({ prompt: "count the assets", response: '{"n": 2}' })]}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "planner" }));

    expect(screen.getByText("count the assets")).toBeTruthy();
    expect(screen.getByText('{"n": 2}')).toBeTruthy();
  });

  it("does not offer to expand a step with nothing to show", () => {
    render(<TraceGraph steps={[step({ tool: "capm", kind: "tool" })]} />);

    expect(screen.getByRole("button", { name: "planner" })).toBeDisabled();
  });

  it("marks a refused step distinctly from one that ran", () => {
    render(
      <TraceGraph
        steps={[
          step({ id: "a", seq: 1, status: "ok" }),
          step({ id: "b", seq: 2, status: "refused", agent: "validator" }),
        ]}
      />,
    );

    const refused = screen.getByText("refused");
    expect(refused.className).toContain("text-negative");
    expect(screen.getByText("ok").className).not.toContain("text-negative");
  });

  it("marks a failed step distinctly too", () => {
    render(<TraceGraph steps={[step({ status: "failed" })]} />);

    expect(screen.getByText("failed").className).toContain("text-negative");
  });

  it("labels a retry as an attempt rather than as new work", () => {
    render(
      <TraceGraph
        steps={[
          step({ id: "a", seq: 1 }),
          step({ id: "b", seq: 2, parent_id: "a", attempt: 2, status: "failed" }),
        ]}
      />,
    );

    expect(screen.getByText("attempt 2")).toBeTruthy();
  });

  it("totals the cost from the steps themselves", () => {
    render(
      <TraceGraph
        steps={[
          step({ id: "a", seq: 1, cost_usd: 0.25 }),
          step({ id: "b", seq: 2, cost_usd: 0.5 }),
        ]}
      />,
    );

    expect(screen.getByTestId("trace-cost").textContent).toBe("$0.7500");
  });

  it("does not print the model twice", () => {
    // Looking at it caught this: the step named its model and then the
    // provider column named it again, which reads as a stutter.
    render(<TraceGraph steps={[step()]} />);

    expect(screen.getAllByText(/ministral-3:8b/)).toHaveLength(1);
  });

  it("names the tool on a tool step", () => {
    render(<TraceGraph steps={[step({ tool: "capm", kind: "tool", provider: null, model: null })]} />);

    expect(screen.getByText("capm")).toBeTruthy();
  });

  it("says so when a run left no trace", () => {
    render(<TraceGraph steps={[]} />);

    expect(screen.getByText(/left no trace/i)).toBeTruthy();
  });
});
