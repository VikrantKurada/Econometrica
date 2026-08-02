import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { makeDataset } from "../../test/fakeApi";
import { DatasetList } from "./DatasetList";

describe("DatasetList", () => {
  it("renders a row per dataset with its facts", () => {
    render(
      <DatasetList
        datasets={[
          makeDataset("p1", { name: "prices.csv", rows: 1506, symbols: ["AAA", "BBB"] }),
        ]}
      />,
    );

    expect(screen.getByText("prices.csv")).toBeInTheDocument();
    expect(screen.getByText(/1,506 rows/)).toBeInTheDocument();
    expect(screen.getByText(/AAA, BBB/)).toBeInTheDocument();
  });

  it("shows an empty hint when a project has no data", () => {
    render(<DatasetList datasets={[]} />);

    expect(screen.getByText(/No data yet/)).toBeInTheDocument();
  });
});
