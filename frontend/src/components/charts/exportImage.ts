/**
 * Exporting a chart as an image.
 *
 * This lives in the browser rather than behind an API on purpose. The fourteen
 * renderers are TypeScript, so a server could only produce a faithful PNG by
 * reimplementing all of them — and the image a reader wants is the one they
 * are looking at, with the theme they are in and the zoom they set. Plotly
 * draws from the live graph, so what downloads is what is on screen.
 *
 * The data exports — CSV, XLSX, JSON, Markdown, the project ZIP — are the
 * opposite case and are served by the backend, which owns the stored outcome
 * and the manifest that has to travel with it.
 */

import Plotly from "./plotly";

/** Raster exports are looked at closely once pasted into a document. */
const RASTER_SCALE = 2;

/**
 * A title as a filename: lowercased, punctuation collapsed to hyphens.
 *
 * Titles are model-authored, so this is a sanitiser and not a formatter — a
 * path separator in a chart title must not become a path in the download.
 */
export function chartFilename(title: string, format: "png" | "svg"): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return `${slug || "chart"}.${format}`;
}

/** The graph Plotly attached to inside `card`, if it has drawn one. */
export function graphIn(card: HTMLElement | null): HTMLElement | null {
  return card?.querySelector<HTMLElement>('[data-testid="plotly-figure"]') ?? null;
}

export async function downloadChartImage(
  graph: HTMLElement,
  title: string,
  format: "png" | "svg",
): Promise<void> {
  const url = await Plotly.toImage(graph, {
    format,
    // Plotly needs explicit dimensions; the rendered box is what to match.
    width: graph.clientWidth || 900,
    height: graph.clientHeight || 400,
    scale: format === "png" ? RASTER_SCALE : 1,
  });

  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = chartFilename(title, format);
  // Appended because Firefox ignores a click on an unattached anchor, and
  // removed immediately so the document is left as it was found.
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
}
