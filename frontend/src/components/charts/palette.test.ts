import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { SERIES_DARK, SERIES_LIGHT } from "./palette";

// The stylesheet as text, not as styles: this suite checks what is *declared*.
// It comes off disk rather than through an import because the suite runs with
// `css: false`, which stubs every stylesheet — including `?raw` — to "".
// Vitest's root is the frontend package; see vite.config.ts.
const css = readFileSync(resolve(process.cwd(), "src/styles/index.css"), "utf8");

/** The custom properties declared inside the first block matching `selector`. */
function tokensIn(selector: string): Record<string, string> {
  const start = css.indexOf(selector);
  if (start === -1) throw new Error(`no block for ${selector} in index.css`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  const body = css.slice(open + 1, close);

  const tokens: Record<string, string> = {};
  for (const [, name, value] of body.matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    tokens[name] = value.trim();
  }
  return tokens;
}

const SLOTS = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => `--series-${n}`);

describe("the series palette", () => {
  it("declares all eight slots for both themes", () => {
    expect(Object.keys(tokensIn(":root {"))).toEqual(expect.arrayContaining(SLOTS));
    expect(Object.keys(tokensIn(':root[data-theme="dark"]'))).toEqual(
      expect.arrayContaining(SLOTS),
    );
  });

  it("also declares the dark slots under prefers-color-scheme", () => {
    // The app always stamps data-theme, so this block is dead inside the shell.
    // It is not dead for an exported chart (task 5.5), which is standalone HTML
    // with no stamp and must still honour the reader's OS setting.
    expect(Object.keys(tokensIn("@media (prefers-color-scheme: dark)"))).toEqual(
      expect.arrayContaining(SLOTS),
    );
  });

  it("uses the validated hexes rather than eyeballed values", () => {
    // The palette is only safe as the exact steps the validator was run on:
    // re-stepping one silently invalidates the recorded colour-blindness
    // separations. The CSS and the TypeScript fallback must not drift apart.
    const light = tokensIn(":root {");
    const dark = tokensIn(':root[data-theme="dark"]');
    expect(SLOTS.map((slot) => light[slot])).toEqual(SERIES_LIGHT);
    expect(SLOTS.map((slot) => dark[slot])).toEqual(SERIES_DARK);
  });

  it("never paints a series with the accent colour", () => {
    // oklch(0.55 0.14 250), the UI accent, is also selection state. A series
    // wearing it makes identity and "you clicked this" the same signal.
    const accent = "#1f74bf";
    expect(SERIES_LIGHT).not.toContain(accent);
    expect(SERIES_DARK).not.toContain(accent);
  });
});
