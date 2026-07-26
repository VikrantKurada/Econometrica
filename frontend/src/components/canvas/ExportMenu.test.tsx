import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ExportMenu } from "./ExportMenu";

describe("ExportMenu", () => {
  it("offers every format the backend serves, for this run", () => {
    render(<ExportMenu runId="run-1" />);

    const links = screen.getAllByRole("link");
    const formats = links.map((link) => link.getAttribute("href"));
    expect(formats).toEqual([
      "/api/runs/run-1/export?format=markdown",
      "/api/runs/run-1/export?format=csv",
      "/api/runs/run-1/export?format=xlsx",
      "/api/runs/run-1/export?format=json",
      "/api/runs/run-1/export?format=zip",
    ]);
  });

  it("asks the browser to save rather than to navigate", () => {
    render(<ExportMenu runId="run-1" />);

    for (const link of screen.getAllByRole("link")) {
      expect(link).toHaveAttribute("download");
    }
  });

  it("says that every download carries its manifest", () => {
    // The claim the project is built on. A reader deciding whether to share a
    // file should be able to see that its provenance travels with it.
    render(<ExportMenu runId="run-1" />);

    expect(screen.getByText(/carries the manifest that reproduces it/i)).toBeInTheDocument();
  });
});
