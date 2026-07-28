import AdmZip from "adm-zip";
import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * Phase 5 gate: a question becomes a chart a person can read, interrogate and
 * take away with them.
 *
 * Browser-level, unlike Phase 4's — the canvas gave runs a UI, so the whole
 * path is now drivable the way a user drives it: type the question, pick the
 * model, watch the phases, read the chart, switch it to a table, open it full
 * screen, and download the archive. Everything under it is real: uvicorn, a
 * live Ollama model planning the analysis, the tool registry computing it, the
 * gates refusing what the data will not support, Postgres holding the trace,
 * and the export route rebuilding it all from the stored outcome.
 *
 * The prices are generated, and the gate asserts that the canvas says so.
 * `playwright.config.ts` sets `ECONOMETRICA_PRICE_SOURCE=synthetic` on the
 * backend it starts, and a canvas that rendered those prices as though they
 * were market data would undo the honesty the Data Steward exists for.
 */

const stamp = Date.now();
const projectName = `E2E canvas ${stamp}`;
const QUESTION = "Test whether this asset follows a random walk over the sample.";

/** Same reasoning as the Phase 4 gate: this needs a model that can follow a schema. */
const CAPABLE_MODELS = [
  "ministral-3",
  "mistral-small",
  "qwen3-coder",
  "qwen2.5-coder",
  "devstral",
  "llama3.1",
  "glm-4",
];

interface ProviderStatus {
  name: string;
  reachable: boolean;
}

interface ModelInfo {
  id: string;
  capabilities: { streaming: boolean };
}

async function pickPlanningModel(request: APIRequestContext): Promise<string | null> {
  const providers = await request.get("/api/providers");
  if (!providers.ok()) return null;
  const ollama = ((await providers.json()) as ProviderStatus[]).find(
    (provider) => provider.name === "ollama" && provider.reachable,
  );
  if (!ollama) return null;

  const listed = await request.get("/api/providers/ollama/models");
  if (!listed.ok()) return null;
  const models = ((await listed.json()) as ModelInfo[]).filter((m) => m.capabilities.streaming);

  const override = process.env.E2E_ANALYSIS_MODEL;
  if (override) {
    if (!models.some((model) => model.id === override)) {
      throw new Error(`E2E_ANALYSIS_MODEL=${override} is not served by this Ollama`);
    }
    return override;
  }

  for (const wanted of CAPABLE_MODELS) {
    const match = models.find((model) => model.id.startsWith(wanted));
    if (match) return match.id;
  }
  return null;
}

async function seedChat(request: APIRequestContext): Promise<void> {
  const project = await (
    await request.post("/api/projects", { data: { name: projectName } })
  ).json();
  await request.post(`/api/projects/${project.id}/chats`, { data: { name: "canvas" } });
}

async function openTheChat(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("treeitem", { name: projectName }).click();
  await page.getByRole("button", { name: `Expand ${projectName}` }).click();
  // Exact: the chat's name is a substring of its project's, and getByRole
  // matches accessible names loosely.
  await page.getByRole("treeitem", { name: "canvas", exact: true }).click();
}

async function cleanUp(request: APIRequestContext): Promise<void> {
  const response = await request.get("/api/projects");
  if (!response.ok()) return;
  for (const project of (await response.json()) as { id: string; name: string }[]) {
    if (project.name.includes(String(stamp))) {
      await request.delete(`/api/projects/${project.id}`);
    }
  }
}

test.afterEach(async ({ request }) => {
  await cleanUp(request);
});

test("a question becomes a chart, a table and an archive", async ({ page, request }) => {
  // Three real model calls plus the econometrics; a cold model load alone can
  // take a minute.
  test.setTimeout(600_000);

  const model = await pickPlanningModel(request);
  if (model === null) {
    test.skip(
      true,
      "no locally served model can plan a structured analysis — pull one of " +
        `${CAPABLE_MODELS.join(", ")}, or set E2E_ANALYSIS_MODEL. Ollama must be running.`,
    );
    return;
  }

  await seedChat(request);
  await openTheChat(page);

  // --- ask ------------------------------------------------------------------

  await page.getByLabel("Question").fill(QUESTION);
  await page.getByLabel("Analysis model").selectOption(model);
  await page.getByRole("button", { name: "Run analysis" }).click();

  // The phases arrive over SSE while the pipeline works.
  await expect(page.getByText(/plan · finished/)).toBeVisible({ timeout: 540_000 });

  // --- read -----------------------------------------------------------------

  const canvas = page.getByTestId("canvas");
  const tabs = canvas.getByRole("tab");
  await expect(tabs.first()).toBeVisible({ timeout: 540_000 });

  // Generated prices must announce themselves, whichever tab is open.
  await expect(canvas.getByRole("alert")).toContainText(/synthetic data/i);

  // A run of a random walk always produces at least one chart: the plans these
  // models write reach for variance_ratio, acf or a volatility path, and every
  // one of those carries a series. If none did, the canvas says so instead —
  // and that message failing to appear alongside no chart is a real failure.
  const drawn = await canvas.locator(".js-plotly-plot").count();
  if (drawn === 0) {
    await expect(canvas.getByText(/produced no charts/i)).toBeVisible();
    test.info().annotations.push({
      type: "note",
      description: "the model planned only hypothesis tests, so there was nothing to draw",
    });
  } else {
    await expect(canvas.locator(".js-plotly-plot").first()).toBeVisible();
  }

  // --- interrogate ----------------------------------------------------------

  if (drawn > 0) {
    // Every chart has a table view: the relief for the light-mode contrast
    // warning and the path to a value without hovering a coloured mark.
    await canvas.getByRole("button", { name: "Table" }).first().click();
    await expect(canvas.getByRole("table").first()).toBeVisible();
    await canvas.getByRole("button", { name: "Chart" }).first().click();

    await canvas.getByRole("button", { name: /^Full screen/ }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.locator(".js-plotly-plot")).toBeVisible();
    await page.getByRole("button", { name: "Close full screen" }).click();
    await expect(dialog).toBeHidden();
  }

  // The diagnostics are always reachable, chart or no chart.
  //
  // Panels are force-mounted since Task 6.14 so a printed report gets all of
  // them, and an inactive one is parked off-screen rather than hidden — a
  // Plotly chart in `display: none` renders blank. So a bare `tabpanel`
  // locator is ambiguous and has to name the active one.
  const activePanel = canvas.locator('[role="tabpanel"][data-state="active"]');
  await canvas.getByRole("tab", { name: "Diagnostics" }).click();
  await expect(activePanel).toBeVisible();

  // A DAG since Task 6.10, not a table: `TraceTable` sorted by sequence and
  // dropped `parent_id`, which made a retry read as new work rather than as a
  // second attempt at the same step.
  await canvas.getByRole("tab", { name: "Trace" }).click();
  await expect(canvas.getByRole("list", { name: "Run trace" })).toBeVisible();

  // --- take it away ---------------------------------------------------------

  await canvas.getByRole("button", { name: "Export" }).click();
  const download = await Promise.race([
    page.waitForEvent("download"),
    canvas.getByRole("link", { name: /Archive/ }).click().then(() => page.waitForEvent("download")),
  ]);

  const archivePath = await download.path();
  expect(download.suggestedFilename()).toMatch(/^econometrica-run-.*\.zip$/);

  // The file opens, and carries the provenance that makes it worth keeping.
  const archive = new AdmZip(archivePath);
  const names = archive.getEntries().map((entry) => entry.entryName);
  expect(names).toEqual(
    expect.arrayContaining(["manifest.json", "run.json", "report.md", "series.csv", "results.xlsx"]),
  );

  const manifest = JSON.parse(archive.readAsText("manifest.json")) as {
    data: { source: string; fingerprint: string; flags: { code: string }[] };
    tools: { tool: string; data_fingerprint: string }[];
  };
  expect(manifest.data.source).toContain("synthetic");
  expect(manifest.data.fingerprint).not.toBe("");
  expect(manifest.data.flags.map((flag) => flag.code)).toContain("synthetic_data");

  // Whatever ran, it is named with the data it ran on — that is the claim the
  // whole project rests on, checked here on the file a user walks away with.
  const ran = manifest.tools.filter((tool) => tool.data_fingerprint !== "");
  expect(ran.length).toBeGreaterThan(0);

  const report = archive.readAsText("report.md");
  expect(report).toContain(QUESTION);
  expect(report).toMatch(/synthetic data/i);
});
