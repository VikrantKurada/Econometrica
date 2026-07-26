import { describe, expect, it } from "vitest";

import { toPlotlyColor } from "./color";

describe("toPlotlyColor", () => {
  it("converts the project's oklch tokens to hex", () => {
    // Plotly's colour parser predates CSS Color 4 and reads oklch() as invalid,
    // which it renders as black rather than as an error. Every token that
    // reaches a trace goes through here.
    expect(toPlotlyColor("oklch(0.985 0 0)")).toBe("#fafafa");
    expect(toPlotlyColor("oklch(0.19 0.005 250)")).toBe("#121416");
    expect(toPlotlyColor("oklch(0.55 0.14 250)")).toBe("#1f74bf");
  });

  it("agrees with the surfaces the palette was validated against", () => {
    // If either of these ever moves, the recorded contrast run is stale and
    // the palette has to be re-validated against the new surface.
    expect(toPlotlyColor("oklch(0.985 0 0)")).toBe("#fafafa");
    expect(toPlotlyColor("oklch(0.19 0.005 250)")).toBe("#121416");
  });

  it("leaves colours Plotly already understands alone", () => {
    expect(toPlotlyColor("#2a78d6")).toBe("#2a78d6");
    expect(toPlotlyColor("rgb(42, 120, 214)")).toBe("rgb(42, 120, 214)");
    expect(toPlotlyColor("rgba(0,0,0,0)")).toBe("rgba(0,0,0,0)");
  });

  it("clamps a token outside the sRGB gamut instead of emitting a broken hex", () => {
    expect(toPlotlyColor("oklch(0.9 0.4 140)")).toMatch(/^#[0-9a-f]{6}$/);
  });

  it("survives a token that is missing or unparseable", () => {
    expect(toPlotlyColor("", "#123456")).toBe("#123456");
    expect(toPlotlyColor("oklch(nonsense)", "#123456")).toBe("#123456");
  });
});
