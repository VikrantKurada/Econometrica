import { render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FIXTURE_RESULT, GALLERY } from "./fixtures";
import { SERIES_DARK, SERIES_LIGHT } from "./palette";
import type { LineChartSpec } from "./spec";
import { LineChart } from "./types/line";

const react = vi.fn();
const purge = vi.fn();
vi.mock("./plotly", () => ({
  default: {
    react: (...args: unknown[]) => react(...args),
    purge: (...args: unknown[]) => purge(...args),
    register: vi.fn(),
  },
}));

const spec = GALLERY.find((s) => s.type === "line") as LineChartSpec;

/** The colour of the first trace in the nth call to Plotly. */
function paintedColor(call: number): unknown {
  const data = react.mock.calls[call][1] as { line?: { color?: string } }[];
  return data[0].line?.color;
}

beforeEach(() => {
  react.mockClear();
  purge.mockClear();
  document.documentElement.dataset.theme = "light";
});

afterEach(() => document.documentElement.removeAttribute("data-theme"));

describe("PlotlyFigure", () => {
  it("repaints from the tokens when the theme changes", async () => {
    // The theme toggle stamps data-theme and every other surface in the app
    // repaints through CSS. Plotly renders to SVG attributes, which do not
    // re-resolve var() — so this observer is the chart's equivalent, and it
    // lives in one place rather than in fourteen components.
    render(<LineChart spec={spec} result={FIXTURE_RESULT} />);
    await waitFor(() => expect(react).toHaveBeenCalledTimes(1));
    expect(paintedColor(0)).toBe(SERIES_LIGHT[0]);

    document.documentElement.dataset.theme = "dark";

    await waitFor(() => expect(react).toHaveBeenCalledTimes(2));
    expect(paintedColor(1)).toBe(SERIES_DARK[0]);
  });

  it("hands the plot back when it goes away", async () => {
    // Plotly keeps listeners and a WebGL-adjacent context per graph div; the
    // canvas mounts and unmounts these as tabs change.
    const view = render(<LineChart spec={spec} result={FIXTURE_RESULT} />);
    await waitFor(() => expect(react).toHaveBeenCalled());

    view.unmount();
    expect(purge).toHaveBeenCalledTimes(1);
  });

  it("stops observing the theme once unmounted", async () => {
    const view = render(<LineChart spec={spec} result={FIXTURE_RESULT} />);
    await waitFor(() => expect(react).toHaveBeenCalledTimes(1));

    view.unmount();
    document.documentElement.dataset.theme = "dark";

    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(react).toHaveBeenCalledTimes(1);
  });
});
