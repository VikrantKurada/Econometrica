import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The print stylesheet, read off disk.
 *
 * Vitest runs with `css: false`, which stubs stylesheets — including `?raw` —
 * to the empty string, so a test that needs a stylesheet's text has to read the
 * file. Same reason `palette.test.ts` does.
 *
 * These assert the *decisions*, not the formatting: that dark theme cannot
 * reach paper, that chrome is dropped, that a chart is never split across a
 * fold. Each is something that would produce a bad print silently.
 */

const CSS = readFileSync(resolve(process.cwd(), "src/styles/print.css"), "utf8");

/** The body of a rule whose selector list contains `needle`. */
function ruleFor(needle: string): string {
  const index = CSS.indexOf(needle);
  expect(index, `no rule mentions ${needle}`).toBeGreaterThan(-1);
  const open = CSS.indexOf("{", index);
  const close = CSS.indexOf("}", open);
  return CSS.slice(open, close);
}

describe("print stylesheet", () => {
  it("only applies to print", () => {
    expect(CSS).toContain("@media print");
  });

  it("forces light surfaces whatever theme the reader was using", () => {
    // A dark canvas sent to a printer is ten pages of toner and grey-on-grey
    // text. The tokens are overridden rather than the rules, so every component
    // follows without knowing about print.
    const dark = ruleFor(':root[data-theme="dark"]');

    expect(dark).toContain("--surface-0: #ffffff");
    expect(dark).toContain("--text-primary: #18181b");
  });

  it("drops the chrome nobody can click on paper", () => {
    const chrome = ruleFor('[role="tablist"]');

    expect(chrome).toContain("display: none");
  });

  it("shows every panel, not only the open tab", () => {
    // On paper there are no tabs, and a report missing the narrative because
    // the reader happened to be on a chart is not a report.
    const panels = ruleFor('[role="tabpanel"][hidden]');

    expect(panels).toContain("display: block");
    expect(panels).toContain("position: static");
  });

  it("never splits a chart across a fold", () => {
    // Half a chart is not a smaller chart, it is a misleading one.
    const block = ruleFor("[data-print-block]");

    expect(block).toContain("break-inside: avoid");
    expect(block).toContain("page-break-inside: avoid");
  });

  it("keeps a table row whole and repeats its header", () => {
    expect(ruleFor("  tr {")).toContain("break-inside: avoid");
    expect(ruleFor("  thead {")).toContain("table-header-group");
  });

  it("unrolls scroll containers so nothing is left past the first screenful", () => {
    const scrollers = ruleFor(".scroll-thin");

    expect(scrollers).toContain("overflow: visible");
    expect(scrollers).toContain("max-height: none");
  });

  it("wraps fingerprints rather than clipping them", () => {
    // A fingerprint you cannot read in full cannot be compared, which is the
    // only thing a fingerprint is for.
    expect(ruleFor("  code,")).toContain("word-break: break-all");
  });

  it("prints the URL behind a link", () => {
    expect(CSS).toContain('a[href^="http"]::after');
    expect(CSS).toContain("attr(href)");
  });

  it("shows the print-only provenance block", () => {
    expect(ruleFor(".print-only")).toContain("display: flex");
  });

  it("sets a page margin", () => {
    expect(CSS).toContain("@page");
    expect(CSS).toMatch(/margin:\s*\d+mm/);
  });
});

describe("screen stylesheet", () => {
  const SCREEN = readFileSync(
    resolve(process.cwd(), "src/styles/index.css"),
    "utf8",
  );

  it("imports the print rules", () => {
    expect(SCREEN).toContain("print.css");
  });

  it("parks a force-mounted inactive panel too, not only a `hidden` one", () => {
    // Radix sets `hidden` on an inactive panel it *unmounts*, and does not on
    // one that is force-mounted — it leaves presence to the consumer. Keying
    // the rule on `[hidden]` alone therefore left the Narrative, Diagnostics
    // and Trace panels rendering in the flow under whichever chart was open,
    // which is exactly what looking at the app showed. `data-state` is set on
    // both.
    expect(SCREEN).toContain('[role="tabpanel"][data-state="inactive"]');
  });

  it("parks an inactive panel off-screen rather than hiding it", () => {
    // `display: none` would give Plotly a zero-width container and it would
    // render blank — so the chart would print empty. Off-screen keeps it
    // measurable.
    const index = SCREEN.indexOf('[role="tabpanel"][hidden]');
    expect(index).toBeGreaterThan(-1);
    const rule = SCREEN.slice(index, SCREEN.indexOf("}", index));

    expect(rule).toContain("display: block");
    expect(rule).toContain("position: absolute");
    expect(rule).not.toContain("display: none");
  });

  it("hides the print-only block on screen", () => {
    expect(SCREEN).toMatch(/\.print-only\s*\{[^}]*display:\s*none/);
  });
});
