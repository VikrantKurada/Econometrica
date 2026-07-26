import { beforeEach, describe, expect, it, vi } from "vitest";

import { chartFilename, downloadChartImage } from "./exportImage";

// jsdom treats a real anchor click as a navigation it has not implemented, so
// every test stubs it; the download itself is verified in a browser.
let click: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});

const toImage = vi.fn(async (..._args: unknown[]) => "data:image/png;base64,AAAA");
vi.mock("./plotly", () => ({
  default: {
    toImage: (...args: unknown[]) => toImage(...args),
    react: vi.fn(),
    purge: vi.fn(),
    register: vi.fn(),
  },
}));

describe("chartFilename", () => {
  it("makes a title safe for a filesystem", () => {
    expect(chartFilename("Variance ratio by horizon", "png")).toBe(
      "variance-ratio-by-horizon.png",
    );
  });

  it("survives a title that is punctuation all the way down", () => {
    expect(chartFilename("?!/\\:*", "svg")).toBe("chart.svg");
  });

  it("does not let a title escape the download directory", () => {
    // Titles come from a model. A path separator in one must not become a path.
    expect(chartFilename("../../etc/passwd", "png")).toBe("etc-passwd.png");
  });
});

describe("downloadChartImage", () => {
  it("asks Plotly for the format the reader chose", async () => {
    const graph = document.createElement("div");

    await downloadChartImage(graph, "Rolling beta", "svg");

    expect(toImage).toHaveBeenCalledWith(graph, expect.objectContaining({ format: "svg" }));
  });

  it("exports a raster at twice the displayed size", async () => {
    // A PNG pasted into a document is looked at closely; the on-screen pixel
    // count is not enough for it.
    const graph = document.createElement("div");

    await downloadChartImage(graph, "Rolling beta", "png");

    expect(toImage).toHaveBeenCalledWith(graph, expect.objectContaining({ scale: 2 }));
  });

  it("hands the browser a named download", async () => {
    const graph = document.createElement("div");

    await downloadChartImage(graph, "Rolling beta", "png");

    expect(click).toHaveBeenCalled();
    // And leaves nothing behind in the document it borrowed.
    expect(document.querySelectorAll("a[download]")).toHaveLength(0);
  });
});
