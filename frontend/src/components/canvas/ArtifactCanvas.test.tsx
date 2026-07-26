import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RunDetail } from "../../lib/types";
import { ArtifactCanvas } from "./ArtifactCanvas";
import { FIXTURE_RUN } from "./fixtures";

vi.mock("../charts/plotly", () => ({
  default: { react: vi.fn(), purge: vi.fn(), register: vi.fn() },
}));

function run(overrides: Partial<RunDetail> = {}): RunDetail {
  return { ...FIXTURE_RUN, ...overrides };
}

describe("ArtifactCanvas", () => {
  it("gives every chart a tab", async () => {
    render(<ArtifactCanvas run={run()} />);

    const tabs = await screen.findAllByRole("tab");
    const labels = tabs.map((tab) => tab.textContent);
    expect(labels).toContain("Volatility estimates");
    expect(labels).toContain("Narrative");
    expect(labels).toContain("Trace");
  });

  it("shows the synthetic-data flag without being asked", async () => {
    // The one thing a canvas must never bury. Rendering generated prices as
    // though they were market data undoes the Data Steward entirely, so this
    // is on screen beside the charts, not behind a tab.
    render(<ArtifactCanvas run={run()} />);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/generated, not observed/);
  });

  it("keeps the risk flag visible whichever tab is open", async () => {
    const user = userEvent.setup();
    render(<ArtifactCanvas run={run()} />);

    await user.click(await screen.findByRole("tab", { name: "Trace" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/generated, not observed/);
  });

  it("reports a refused step as a finding rather than an absence", async () => {
    // A canvas showing only what ran would misrepresent the analysis: the
    // refusal is the most informative thing the run produced.
    render(<ArtifactCanvas run={run()} />);

    const findings = await screen.findByRole("region", { name: "Findings" });
    expect(findings).toHaveTextContent("hurst");
    expect(findings).toHaveTextContent(/needs 500 observations/);
  });

  it("keeps an unjudged check apart from a refusal", async () => {
    render(<ArtifactCanvas run={run()} />);

    const findings = await screen.findByRole("region", { name: "Findings" });
    expect(within(findings).getByText(/not judged/i)).toBeInTheDocument();
    expect(findings).toHaveTextContent(/could not run on a series this short/);
  });

  it("explains a withheld interpretation instead of showing nothing", async () => {
    const user = userEvent.setup();
    const withheld = run({
      outcome: {
        ...FIXTURE_RUN.outcome,
        narration: {
          published: false,
          narrative: { prose: "The beta is 1.42.", citations: [] },
          grounding: {
            grounded: false,
            checked: 3,
            issues: [{ value: 1.42, text: "1.42", sentence: "The beta is 1.42." }],
          },
        },
      },
    });

    render(<ArtifactCanvas run={withheld} />);
    await user.click(await screen.findByRole("tab", { name: "Narrative" }));

    expect(screen.getByText(/withheld/i)).toBeInTheDocument();
    // The number that matched nothing, and the sentence it was written in —
    // enough for a reader to see what the model claimed and why it was held.
    const issue = within(screen.getByRole("tabpanel")).getByRole("listitem");
    expect(issue).toHaveTextContent("1.42");
    expect(issue).toHaveTextContent("The beta is 1.42.");
  });

  it("pins an artifact so it stays visible on another tab", async () => {
    const user = userEvent.setup();
    render(<ArtifactCanvas run={run()} />);

    await user.click(await screen.findByRole("button", { name: /^Pin/ }));
    await user.click(screen.getByRole("tab", { name: "Trace" }));

    const pinned = screen.getByRole("region", { name: "Pinned" });
    expect(within(pinned).getByRole("figure", { name: "Volatility estimates" })).toBeInTheDocument();
  });

  it("opens an artifact full screen", async () => {
    const user = userEvent.setup();
    render(<ArtifactCanvas run={run()} />);

    await user.click(await screen.findByRole("button", { name: /^Full screen/ }));

    expect(await screen.findByRole("dialog", { name: "Volatility estimates" })).toBeInTheDocument();
  });

  it("re-runs the analysis and reports that it reproduced", async () => {
    const user = userEvent.setup();
    const onRerun = vi.fn().mockResolvedValue({
      run_id: FIXTURE_RUN.id,
      reproduced: true,
      steps: [],
    });

    render(<ArtifactCanvas run={run()} onRerun={onRerun} />);
    await user.click(await screen.findByRole("button", { name: "Re-run" }));

    expect(onRerun).toHaveBeenCalledWith(FIXTURE_RUN.id);
    expect(await screen.findByText(/reproduced/i)).toBeInTheDocument();
  });

  it("names the step that stopped reproducing", async () => {
    // "Did not reproduce" without saying which step and why is a dead end.
    const user = userEvent.setup();
    const onRerun = vi.fn().mockResolvedValue({
      run_id: FIXTURE_RUN.id,
      reproduced: false,
      steps: [
        {
          step_id: "s1",
          tool: "garch",
          reproduced: false,
          status: "ran",
          original_status: "ran",
          data_fingerprint: "sha256:beef",
          original_data_fingerprint: "sha256:9f2c",
          params_hash: "a41e",
          original_params_hash: "a41e",
          detail: "the data fingerprint changed",
        },
      ],
    });

    render(<ArtifactCanvas run={run()} onRerun={onRerun} />);
    await user.click(await screen.findByRole("button", { name: "Re-run" }));

    const report = await screen.findByRole("status");
    expect(report).toHaveTextContent("s1");
    expect(report).toHaveTextContent("the data fingerprint changed");
  });

  it("says so when a run produced no chart at all", async () => {
    const bare = run({ outcome: { ...FIXTURE_RUN.outcome, charts: [] } });

    render(<ArtifactCanvas run={bare} />);

    expect(await screen.findByText(/no charts/i)).toBeInTheDocument();
  });
});
