import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunOutcome } from "../../lib/types";
import { Diagnostics } from "./Diagnostics";
import { FIXTURE_RUN } from "./fixtures";

const outcome = FIXTURE_RUN.outcome as RunOutcome;

describe("Diagnostics", () => {
  it("shows each check with the numbers behind it", () => {
    // A hypothesis test's whole finding is its statistic and p-value, and
    // neither is a series, a table, an estimate or a scalar — so no chart type
    // can bind to it. Without this panel an `adf` step is invisible.
    render(<Diagnostics outcome={outcome} />);

    const row = screen.getByRole("row", { name: /ljung box/i });
    expect(row).toHaveTextContent("14.2");
    expect(row).toHaveTextContent("0.29");
  });

  it("reads an unjudged check as unjudged, never as a failure", () => {
    // `Diagnostic.passed` is tri-state and the third state is the point:
    // `null` means the tool did not judge it, which is not "it failed".
    render(<Diagnostics outcome={outcome} />);

    const row = screen.getByRole("row", { name: /arch lm/i });
    expect(within(row).getByText(/not judged/i)).toBeInTheDocument();
  });

  it("distinguishes a check that passed from one that did not", () => {
    render(<Diagnostics outcome={outcome} />);

    expect(within(screen.getByRole("row", { name: /ljung box/i })).getByText("passed"))
      .toBeInTheDocument();
    expect(within(screen.getByRole("row", { name: /jarque bera/i })).getByText("failed"))
      .toBeInTheDocument();
  });

  it("carries the tool's own interpretation", () => {
    render(<Diagnostics outcome={outcome} />);

    expect(screen.getByText(/residuals are not normal/i)).toBeInTheDocument();
  });

  it("says so when nothing was checked", () => {
    render(<Diagnostics outcome={{ ...outcome, diagnostics: [] }} />);

    expect(screen.getByText(/no assumption checks/i)).toBeInTheDocument();
  });
});
