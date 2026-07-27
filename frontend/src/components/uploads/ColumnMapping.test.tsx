import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Upload } from "../../lib/types";
import { ColumnMapping } from "./ColumnMapping";

/**
 * The screen exists to keep a person in the loop, so what these tests pin is
 * exactly that: every column is editable, nothing is confirmed on arrival, and
 * the button refuses a mapping the server would reject anyway.
 */

function upload(overrides: Partial<Upload> = {}): Upload {
  return {
    id: "u1",
    project_id: "p1",
    filename: "prices.csv",
    profile: {
      filename: "prices.csv",
      format: "csv",
      rows: 4,
      layout: "wide",
      delimiter: ",",
      columns: [
        {
          name: "date",
          dtype: "datetime",
          present: 4,
          missing: 0,
          unique: 4,
          minimum: null,
          maximum: null,
          sample: ["2024-01-01"],
          parses_as_date: true,
          decimal_comma: false,
          candidates: [{ role: "date", score: 1, reason: "values parse as dates" }],
        },
        {
          name: "AAPL",
          dtype: "number",
          present: 4,
          missing: 0,
          unique: 4,
          minimum: 100,
          maximum: 103,
          sample: ["100.0"],
          parses_as_date: false,
          decimal_comma: false,
          candidates: [{ role: "price", score: 0.6, reason: "strictly positive values" }],
        },
      ],
    },
    proposal: {
      roles: { date: "date", AAPL: "price" },
      rationale: { date: "values parse as dates", AAPL: "strictly positive values" },
      ambiguous: [],
    },
    consulted_model: false,
    confirmed: false,
    mapping: null,
    observations: null,
    symbols: [],
    fields: [],
    ...overrides,
  };
}

describe("ColumnMapping", () => {
  it("shows every column with its suggested role", () => {
    render(<ColumnMapping upload={upload()} onConfirm={vi.fn()} />);

    expect(screen.getByLabelText("Role for date")).toHaveValue("date");
    expect(screen.getByLabelText("Role for AAPL")).toHaveValue("price");
  });

  it("explains why each column got its role", () => {
    render(<ColumnMapping upload={upload()} onConfirm={vi.fn()} />);

    expect(screen.getByText("strictly positive values")).toBeTruthy();
  });

  it("lets every column be changed", async () => {
    const onConfirm = vi.fn();
    render(<ColumnMapping upload={upload()} onConfirm={onConfirm} />);

    await userEvent.selectOptions(screen.getByLabelText("Role for AAPL"), "return");
    await userEvent.click(screen.getByRole("button", { name: /confirm/i }));

    expect(onConfirm).toHaveBeenCalledWith({ date: "date", AAPL: "return" });
  });

  it("marks the columns where the choice was genuinely close", () => {
    const data = upload();
    data.proposal.ambiguous = ["AAPL"];

    render(<ColumnMapping upload={data} onConfirm={vi.fn()} />);

    expect(screen.getByTitle(/more than one role/i)).toBeTruthy();
  });

  it("refuses a mapping with no date column", async () => {
    render(<ColumnMapping upload={upload()} onConfirm={vi.fn()} />);

    await userEvent.selectOptions(screen.getByLabelText("Role for date"), "ignore");

    expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
    expect(screen.getByText(/map one column as the date/i)).toBeTruthy();
  });

  it("refuses a mapping with two date columns", async () => {
    render(<ColumnMapping upload={upload()} onConfirm={vi.fn()} />);

    await userEvent.selectOptions(screen.getByLabelText("Role for AAPL"), "date");

    expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
    expect(screen.getByText(/only one column can be the date/i)).toBeTruthy();
  });

  it("refuses a mapping with no values", async () => {
    render(<ColumnMapping upload={upload()} onConfirm={vi.fn()} />);

    await userEvent.selectOptions(screen.getByLabelText("Role for AAPL"), "ignore");

    expect(screen.getByRole("button", { name: /confirm/i })).toBeDisabled();
    expect(screen.getByText(/at least one column as a price/i)).toBeTruthy();
  });

  it("says nothing is stored until the user confirms", () => {
    render(<ColumnMapping upload={upload()} onConfirm={vi.fn()} />);

    expect(screen.getByText(/nothing is stored until you confirm/i)).toBeTruthy();
  });

  it("reports what a confirmed mapping ingested", () => {
    render(
      <ColumnMapping
        upload={upload({
          confirmed: true,
          observations: 8,
          symbols: ["AAPL", "MSFT"],
          fields: ["price"],
        })}
        onConfirm={vi.fn()}
      />,
    );

    expect(screen.getByText(/8 observations/)).toBeTruthy();
  });

  it("surfaces a rejection from the server", () => {
    render(
      <ColumnMapping upload={upload()} onConfirm={vi.fn()} error="no column named GOOG" />,
    );

    expect(screen.getByRole("alert").textContent).toContain("GOOG");
  });

  it("says when a model suggested the roles", () => {
    render(<ColumnMapping upload={upload({ consulted_model: true })} onConfirm={vi.fn()} />);

    expect(screen.getByText(/suggested by a model/i)).toBeTruthy();
  });
});
