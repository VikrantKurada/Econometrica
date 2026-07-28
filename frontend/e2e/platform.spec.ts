import AdmZip from "adm-zip";
import { expect, request as playwrightRequest, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";

/**
 * Phase 6 gate: the whole workbench, on data nobody generated.
 *
 * Every earlier gate runs on `ECONOMETRICA_PRICE_SOURCE=synthetic`, deliberately
 * — they assert that a run built on generated prices announces itself, and that
 * assertion is only worth having while the generator is what they get. This one
 * runs against **yfinance**, on a second uvicorn `playwright.config.ts` starts
 * on port 8101 sharing the same Postgres. So the `synthetic_data` flag is
 * asserted *absent* here, which is the other half of the same honesty seam: a
 * flag that fired on real market data would be as bad as one that stayed quiet
 * on generated data.
 *
 * The regression covers, in one pass: a project and a chat; an upload profiled,
 * mapped and confirmed into the observations hypertable; an analysis on a real
 * ticker; the artifacts read back in the browser, including the trace DAG and
 * the cost dashboard; the ZIP export with its manifest; and a re-run that
 * reproduces the numbers from that manifest without asking a model anything.
 */

const stamp = Date.now();
const projectName = `E2E platform ${stamp}`;
const TICKER = "AAPL";
const QUESTION =
  `Test whether ${TICKER} follows a random walk between 2019 and 2023, ` +
  "using monthly data.";

/** Same reasoning as the Phase 4 and 5 gates: this needs a model that can follow a schema. */
const CAPABLE_MODELS = [
  "ministral-3",
  "mistral-small",
  "qwen3-coder",
  "qwen2.5-coder",
  "devstral",
  "llama3.1",
  "glm-4",
];

/** The real-data backend. Absolute, because it is not the one behind the proxy. */
const MARKET_API = "http://127.0.0.1:8101";

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

/**
 * Real prices need a reachable vendor, and an unreachable one is a fact about
 * the network rather than a regression. Skipping is the project's convention
 * for live checks — read the report, not just the exit code.
 */
async function yahooIsReachable(): Promise<boolean> {
  try {
    const response = await fetch(
      `https://query1.finance.yahoo.com/v8/finance/chart/${TICKER}?range=5d&interval=1d`,
      { signal: AbortSignal.timeout(8_000) },
    );
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * A small CSV in the shape a user's own export takes: a date column, two
 * tickers of closes. Deliberately unambiguous, so the profiler needs no model
 * and the upload path is exercised without a second billed turn.
 */
function priceCsv(): Buffer {
  const rows = ["Date,AAA,BBB"];
  for (let day = 1; day <= 40; day += 1) {
    const date = new Date(Date.UTC(2024, 0, day));
    const iso = date.toISOString().slice(0, 10);
    rows.push(`${iso},${(100 + day * 0.5).toFixed(2)},${(50 + day * 0.25).toFixed(2)}`);
  }
  return Buffer.from(rows.join("\n"), "utf-8");
}

interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

/**
 * The same parser as the Phase 4 gate's.
 *
 * A naive split on blank lines mishandles CRLF and a multi-line `data:` frame,
 * and the failure mode is a last event that is not `run.finished` — which
 * reads as a run that died rather than as a parser that lost the end of it.
 */
function parseSse(body: string): SseEvent[] {
  const events: SseEvent[] = [];
  for (const block of body.replace(/\r\n/g, "\n").split("\n\n")) {
    let name = "";
    const data: string[] = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) name = line.slice("event:".length).trim();
      else if (line.startsWith("data:")) data.push(line.slice("data:".length).trim());
    }
    if (data.length > 0) {
      events.push({ event: name, data: JSON.parse(data.join("\n")) as Record<string, unknown> });
    }
  }
  return events;
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

async function openTheChat(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("treeitem", { name: projectName }).click();
  await page.getByRole("button", { name: `Expand ${projectName}` }).click();
  await page.getByRole("treeitem", { name: "platform", exact: true }).click();
}

test.afterEach(async ({ request }) => {
  await cleanUp(request);
});

test("the whole workbench, on real market data", async ({ page, request }) => {
  // A cold model load, a network fetch of six years of prices, three model
  // calls and a re-run.
  test.setTimeout(900_000);

  const model = await pickPlanningModel(request);
  if (model === null) {
    test.skip(
      true,
      "no locally served model can plan a structured analysis — pull one of " +
        `${CAPABLE_MODELS.join(", ")}, or set E2E_ANALYSIS_MODEL. Ollama must be running.`,
    );
    return;
  }
  if (!(await yahooIsReachable())) {
    test.skip(true, "Yahoo is unreachable, so there is no real market data to run on");
    return;
  }

  const market = await playwrightRequest.newContext({ baseURL: MARKET_API });

  // --- a project and a chat -------------------------------------------------

  const project = await (
    await request.post("/api/projects", { data: { name: projectName } })
  ).json();
  await request.patch(`/api/projects/${project.id}`, {
    data: {
      validation_tier: "critic",
      model_assignments: Object.fromEntries(
        ["planner", "validator", "narrator"].map((role) => [
          role,
          { provider: "ollama", model },
        ]),
      ),
    },
  });
  const chat = await (
    await request.post(`/api/projects/${project.id}/chats`, { data: { name: "platform" } })
  ).json();

  // --- an upload, profiled and confirmed ------------------------------------

  const uploaded = await request.post(`/api/projects/${project.id}/uploads`, {
    multipart: {
      file: { name: "prices.csv", mimeType: "text/csv", buffer: priceCsv() },
    },
  });
  expect(uploaded.status(), await uploaded.text()).toBe(201);
  const upload = await uploaded.json();

  // The profiler decides nothing; it scores what each column *could* be. An
  // unambiguous file therefore needs no model at all, which is the common case
  // and has to stay free.
  expect(upload.consulted_model).toBe(false);
  expect(upload.proposal.roles.Date).toBe("date");
  expect(upload.confirmed).toBe(false);

  const confirmed = await request.post(`/api/uploads/${upload.id}/confirm`, {
    data: { roles: upload.proposal.roles },
  });
  expect(confirmed.status(), await confirmed.text()).toBe(200);
  const stored = await confirmed.json();
  expect(stored.confirmed).toBe(true);

  // Only a confirmed mapping is ingested, so the dataset appears only now.
  const datasets = await (await request.get(`/api/projects/${project.id}/datasets`)).json();
  expect(datasets).toHaveLength(1);
  expect(datasets[0].symbols.sort()).toEqual(["AAA", "BBB"]);
  expect(datasets[0].rows).toBe(80);
  // The Data Steward's risk flag fires on this substring, so a user's own file
  // reported as generated would be as wrong as the reverse.
  expect(datasets[0].source_label.toLowerCase()).not.toContain("synthetic");

  // --- an analysis on a real ticker -----------------------------------------

  const started = await market.post(`/api/chats/${chat.id}/runs`, {
    data: { question: QUESTION },
    timeout: 900_000,
  });
  expect(started.status(), await started.text()).toBe(200);

  const events = parseSse(await started.text());
  const names = events.map((event) => event.event);
  expect(names.at(-1), `no run.finished; got ${names.join(", ")}`).toBe("run.finished");

  const outcome = events.at(-1)!.data.payload as Record<string, any>;

  expect(
    outcome.status,
    `the run failed: ${outcome.error}. If this names a missing price source, an ` +
      "old e2e backend is being reused on 8101 — stop it and rerun.",
  ).not.toBe("failed");

  const planned = outcome.execution.outcomes as { tool: string; status: string }[];
  console.log(
    `[gate] model=${model} status=${outcome.status} source=${outcome.quality.source} ` +
      `rows=${outcome.quality.rows} flags=[${(outcome.quality.flags as { code: string }[])
        .map((flag) => flag.code)
        .join(",")}] ` +
      `plan=${planned.map((step) => `${step.tool}:${step.status}`).join(" ")}`,
  );

  // The other half of the honesty seam: real prices must NOT be flagged.
  const flags = (outcome.quality.flags as { code: string }[]).map((flag) => flag.code);
  expect(flags).not.toContain("synthetic_data");
  expect(outcome.quality.source).toContain("yfinance");
  // And the label names the adjustment policy, because Yahoo's split-adjusted
  // and dividend-adjusted closes for one day can differ by 3%.
  expect(outcome.quality.source).toContain("adjusted");
  expect(outcome.quality.rows).toBeGreaterThan(30);

  const planSteps = outcome.execution.outcomes as { result: unknown | null }[];
  expect(planSteps.filter((step) => step.result !== null).length).toBeGreaterThan(0);

  const runs = await (await request.get(`/api/chats/${chat.id}/runs`)).json();
  expect(runs).toHaveLength(1);
  const runId = runs[0].id as string;

  // --- read it back in the browser ------------------------------------------

  // The canvas shows the chat's latest run, so opening the chat is enough.
  await openTheChat(page);

  const canvas = page.getByTestId("canvas");
  await expect(canvas.getByRole("tab").first()).toBeVisible({ timeout: 60_000 });

  // Real data, so nothing may claim otherwise. `synthetic data` is the alert
  // the earlier gates assert is present; here its absence is the assertion.
  await expect(canvas.getByText(/synthetic data/i)).toHaveCount(0);

  // Panels are force-mounted so the printed report gets all of them, and an
  // inactive one is parked off-screen rather than hidden — so a tabpanel
  // locator has to name the active one.
  const activePanel = canvas.locator('[role="tabpanel"][data-state="active"]');

  // The trace is a DAG, not a list: a retry nests under its first attempt.
  await canvas.getByRole("tab", { name: "Trace" }).click();
  await expect(activePanel.getByText(/planner/i).first()).toBeVisible();
  await expect(activePanel.getByText(model, { exact: false }).first()).toBeVisible();

  // And the cost dashboard, fetched only when its tab is opened.
  await canvas.getByRole("tab", { name: "Cost" }).click();
  await expect(activePanel.getByText(/token/i).first()).toBeVisible({ timeout: 30_000 });

  // --- take it away ---------------------------------------------------------

  const archive = await request.get(`/api/runs/${runId}/export?format=zip`);
  expect(archive.ok()).toBe(true);
  const entries = new AdmZip(Buffer.from(await archive.body()))
    .getEntries()
    .map((entry) => entry.entryName);
  expect(entries).toContain("manifest.json");

  // --- and reproduce it -----------------------------------------------------

  // Re-run consults no model: it re-executes the recorded plan against freshly
  // resolved data and compares manifests and numbers. On real prices that is a
  // sharper check than on generated ones — the cache is what makes the fetch
  // identical, and a stale entry would be a *different* series.
  const rerun = await market.post(`/api/runs/${runId}/rerun`, { timeout: 600_000 });
  expect(rerun.ok(), await rerun.text()).toBe(true);
  const report = await rerun.json();
  expect(
    report.reproduced,
    `re-run did not reproduce: ${JSON.stringify(report.steps, null, 2)}`,
  ).toBe(true);

  await market.dispose();
});
