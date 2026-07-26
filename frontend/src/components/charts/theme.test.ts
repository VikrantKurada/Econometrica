import { afterEach, describe, expect, it } from "vitest";

import { SERIES_DARK, SERIES_LIGHT } from "./palette";
import { baseLayout, readChartTheme, seriesColors } from "./theme";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
});

function element(properties: Record<string, string> = {}): HTMLElement {
  const el = document.createElement("div");
  for (const [name, value] of Object.entries(properties)) el.style.setProperty(name, value);
  document.body.append(el);
  return el;
}

describe("readChartTheme", () => {
  it("takes its colours from the CSS custom properties", () => {
    // The whole point: the theme toggle swaps the tokens and the charts repaint
    // from them. Nothing about the palette is compiled into a component.
    const theme = readChartTheme(element({ "--series-1": "#abcdef", "--border": "#123456" }));

    expect(theme.series[0]).toBe("#abcdef");
    expect(theme.gridline).toBe("#123456");
  });

  it("falls back to the validated palette for the stamped theme", () => {
    // jsdom loads no stylesheet, and an exported chart may carry none either.
    document.documentElement.dataset.theme = "dark";
    expect(readChartTheme(element()).series).toEqual([...SERIES_DARK]);

    document.documentElement.dataset.theme = "light";
    expect(readChartTheme(element()).series).toEqual([...SERIES_LIGHT]);
  });

  it("reports the mode it resolved", () => {
    document.documentElement.dataset.theme = "dark";
    expect(readChartTheme(element()).mode).toBe("dark");
  });
});

describe("seriesColors", () => {
  it("assigns the slots in declared order", () => {
    const theme = readChartTheme(element());
    expect(seriesColors(theme, ["beta", "alpha"])).toEqual({
      beta: SERIES_LIGHT[0],
      alpha: SERIES_LIGHT[1],
    });
  });

  it("folds a ninth series to muted rather than cycling the hues", () => {
    // A generated or reused ninth hue is indistinguishable from a slot under
    // colour-blindness simulation. The documented remedy is to fold the tail.
    const theme = readChartTheme(element());
    const keys = Array.from({ length: 9 }, (_, i) => `s${i}`);
    const colors = seriesColors(theme, keys);

    expect(colors.s8).toBe(theme.muted);
    expect(Object.values(colors).filter((c) => c === SERIES_LIGHT[0])).toHaveLength(1);
  });
});

describe("baseLayout", () => {
  it("draws gridlines as solid recessive hairlines", () => {
    // Dashing reads as "projection" or "threshold" when it is just a grid.
    const layout = baseLayout(readChartTheme(element({ "--border": "#123456" })));

    expect(layout.xaxis?.gridcolor).toBe("#123456");
    expect(layout.xaxis?.gridwidth).toBe(1);
    expect(layout.xaxis).not.toHaveProperty("griddash");
    expect(layout.yaxis).not.toHaveProperty("griddash");
  });

  it("leaves the surface to the card so the validated contrast holds", () => {
    const layout = baseLayout(readChartTheme(element()));
    expect(layout.paper_bgcolor).toBe("rgba(0,0,0,0)");
    expect(layout.plot_bgcolor).toBe("rgba(0,0,0,0)");
  });

  it("puts axis and legend text in ink, never in a series colour", () => {
    const theme = readChartTheme(element());
    const layout = baseLayout(theme);

    expect(layout.font?.color).toBe(theme.textSecondary);
    expect(theme.series).not.toContain(layout.font?.color);
  });
});
