import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Metrics } from "../../lib/types";
import { CostDashboard } from "./CostDashboard";

function metrics(overrides: Partial<Metrics> = {}): Metrics {
  return {
    spans: [],
    tokens: { input: 1000, output: 200, cache_read: 0, cache_write: 0 },
    tokens_by_provider: [],
    tokens_by_agent: [],
    cost_usd: 1.5,
    runs: 4,
    revisions_total: 2,
    revisions_mean: 0.5,
    tool_error_rate: 0.25,
    validator_rejection_rate: null,
    ...overrides,
  };
}

describe("CostDashboard", () => {
  it("shows the totals", () => {
    render(<CostDashboard metrics={metrics()} />);

    expect(screen.getByText("4")).toBeTruthy();
    expect(screen.getByText("1,200")).toBeTruthy();
    expect(screen.getByText("$1.5000")).toBeTruthy();
  });

  it("renders a rate with no denominator as 'not yet', never as zero", () => {
    // 0% would read as "nothing has ever failed", which is a claim about the
    // system. Having run nothing is a statement about the sample.
    render(<CostDashboard metrics={metrics({ validator_rejection_rate: null })} />);

    expect(screen.getByText("not yet")).toBeTruthy();
  });

  it("renders a real zero rate as zero", () => {
    render(
      <CostDashboard
        metrics={metrics({ tool_error_rate: 0, validator_rejection_rate: 0 })}
      />,
    );

    expect(screen.getAllByText("0%").length).toBe(2);
  });

  it("breaks spend down by provider and by role", () => {
    render(
      <CostDashboard
        metrics={metrics({
          tokens_by_provider: [
            { key: "ollama", input: 900, output: 100, cache_read: 0, cache_write: 0, cost_usd: 0 },
          ],
          tokens_by_agent: [
            { key: "planner", input: 500, output: 60, cache_read: 0, cache_write: 0, cost_usd: 1.5 },
          ],
        })}
      />,
    );

    expect(screen.getByText("ollama")).toBeTruthy();
    expect(screen.getByText("planner")).toBeTruthy();
    expect(screen.getByText("1,000 tokens")).toBeTruthy();
  });

  it("shows latency percentiles per operation", () => {
    render(
      <CostDashboard
        metrics={metrics({
          spans: [
            { name: "GET /api/runs/{run_id}", count: 12, p50: 40, p95: 120, p99: 300, error_rate: 0 },
          ],
        })}
      />,
    );

    expect(screen.getByText("GET /api/runs/{run_id}")).toBeTruthy();
    expect(screen.getByText("40ms")).toBeTruthy();
    expect(screen.getByText("300ms")).toBeTruthy();
  });

  it("renders a slow operation in seconds rather than four digits of ms", () => {
    render(
      <CostDashboard
        metrics={metrics({
          spans: [
            { name: "POST /api/chats/{id}/runs", count: 3, p50: 18400, p95: 22000, p99: 22000, error_rate: 0 },
          ],
        })}
      />,
    );

    expect(screen.getByText("18.4s")).toBeTruthy();
  });

  it("marks an operation that has been failing", () => {
    render(
      <CostDashboard
        metrics={metrics({
          spans: [
            { name: "GET /api/runs/{run_id}", count: 4, p50: 5, p95: 5, p99: 5, error_rate: 0.5 },
          ],
        })}
      />,
    );

    expect(screen.getByText("50%").className).toContain("text-negative");
  });

  it("says when nothing has been measured", () => {
    render(<CostDashboard metrics={metrics({ spans: [] })} />);

    expect(screen.getByText(/nothing has been measured/i)).toBeTruthy();
  });
});
