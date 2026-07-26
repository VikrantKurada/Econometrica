import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ResultSet } from "../../lib/types";
import { ChartCard } from "./ChartCard";
import { FIXTURE_RESULT, GALLERY } from "./fixtures";
import type { ChartSpec } from "./spec";

// Plotly draws to real geometry that jsdom does not have. What matters here is
// the frame around it — the card's own behaviour — so the drawing is stubbed
// and the rendering itself is checked in a browser.
const react = vi.fn();
vi.mock("./plotly", () => ({
  default: { react: (...args: unknown[]) => react(...args), purge: vi.fn(), register: vi.fn() },
}));

beforeEach(() => react.mockClear());

const cards = GALLERY.map((spec) => [spec.type, spec] as [string, ChartSpec]);

describe("ChartCard", () => {
  it.each(cards)("%s names itself", (_type, spec) => {
    render(<ChartCard spec={spec} result={FIXTURE_RESULT} />);
    expect(screen.getByRole("figure", { name: spec.title })).toBeInTheDocument();
  });

  it.each(cards)("%s can be read as a table", async (_type, spec) => {
    const user = userEvent.setup();
    render(<ChartCard spec={spec} result={FIXTURE_RESULT} />);

    const table = screen.queryByRole("table");
    if (!table) await user.click(screen.getByRole("button", { name: "Table" }));

    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("shows the values the chart drew", async () => {
    const user = userEvent.setup();
    const spec = GALLERY.find((s) => s.type === "forest")!;
    render(<ChartCard spec={spec} result={FIXTURE_RESULT} />);

    await user.click(screen.getByRole("button", { name: "Table" }));
    const table = await screen.findByRole("table");
    expect(within(table).getByText("mkt_rf")).toBeInTheDocument();
    expect(within(table).getByText("1.08")).toBeInTheDocument();
  });

  it("says why a chart could not be drawn instead of drawing nothing", () => {
    // An empty plot area reads as "no effect found". A spec that does not bind
    // is a broken chart and has to say so.
    const spec = GALLERY.find((s) => s.type === "line")!;
    const empty: ResultSet = { ...FIXTURE_RESULT, series: {} };

    render(<ChartCard spec={spec} result={empty} />);

    expect(screen.getByRole("status")).toHaveTextContent(/realized_vol/);
    expect(react).not.toHaveBeenCalled();
  });

  it("keeps the caption with the chart", () => {
    const spec = { ...GALLERY[0], caption: "Synthetic prices; not market data." };
    render(<ChartCard spec={spec} result={FIXTURE_RESULT} />);
    expect(screen.getByText("Synthetic prices; not market data.")).toBeInTheDocument();
  });
});

describe("the spec union", () => {
  it("renders every chart type the backend can send", () => {
    // The backend owns the vocabulary. If a type is added there and not here,
    // the canvas would drop it silently — so the Python is the fixture.
    const python = readFileSync(
      resolve(process.cwd(), "../backend/src/econometrica/charts/spec.py"),
      "utf8",
    );
    const declared = [...python.matchAll(/type:\s*Literal\["(\w+)"\]/g)].map((m) => m[1]);

    expect(declared.length).toBeGreaterThan(0);
    expect(new Set(GALLERY.map((spec) => spec.type))).toEqual(new Set(declared));
  });
});
