import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * Phase 4 gate: "does this asset follow a random walk?" asked of a real model,
 * answered by real econometrics, with the whole run traced to Postgres.
 *
 * API-level rather than browser-level, because there is no UI for runs yet —
 * the pipeline is reachable over HTTP only. Everything below the UI is real:
 * uvicorn, the Vite proxy, a live Ollama model choosing the methods, the tool
 * registry computing them, the gates refusing what the data will not support,
 * and Postgres holding the trace afterwards.
 *
 * The prices are generated, not market data. That is the point of the
 * `synthetic_data` assertion below: the gate proves the pipeline runs *and*
 * that a run built on generated prices says so in its own quality report.
 * `playwright.config.ts` sets `ECONOMETRICA_PRICE_SOURCE=synthetic` on the
 * backend it starts.
 */

const stamp = Date.now();
const projectName = `E2E analysis ${stamp}`;
const QUESTION = "Test whether this asset follows a random walk over the sample.";

/**
 * A plan is structured JSON over a 36-tool catalogue, which is a much harder
 * ask than the Phase 3 chat gate's "say anything". tinyllama cannot do it, so
 * the smallest-model trick used there does not apply: this needs a model that
 * can follow a schema. Measured on the development machine, ministral-3:8b
 * plans this question correctly in one attempt in about five seconds.
 */
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

interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

interface TraceStep {
  id: string;
  parent_id: string | null;
  agent: string;
  kind: string;
  status: string;
  attempt: number;
  provider: string | null;
  model: string | null;
  output_tokens: number;
  tool: string | null;
  tool_call_hash: string | null;
}

/** The model this run should use, or null if none here can plan. */
async function pickPlanningModel(request: APIRequestContext): Promise<string | null> {
  const providers = await request.get("/api/providers");
  if (!providers.ok()) return null;
  const reachable = ((await providers.json()) as ProviderStatus[]).find(
    (provider) => provider.name === "ollama",
  )?.reachable;
  if (!reachable) return null;

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

/** Parse a complete SSE body into its events. */
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

test.afterEach(async ({ request }) => {
  await cleanUp(request);
});

test("an analysis runs end to end and leaves a full trace", async ({ request }) => {
  // Three real model calls plus the econometrics. A cold model load alone can
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

  // `critic` on purpose: it exercises the Validator, and assigning it the same
  // provider as the Planner is what triggers the independence warning that
  // this run then asserts on.
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
    await request.post(`/api/projects/${project.id}/chats`, { data: { name: "run" } })
  ).json();

  // --- the run ---------------------------------------------------------

  const response = await request.post(`/api/chats/${chat.id}/runs`, {
    data: { question: QUESTION },
    timeout: 590_000,
  });
  expect(response.status()).toBe(200);

  const events = parseSse(await response.text());
  const names = events.map((event) => event.event);

  expect(names[0]).toBe("run.started");
  expect(names.at(-1)).toBe("run.finished");
  for (const phase of ["plan.finished", "data.finished", "validate.finished", "narrate.finished"]) {
    expect(names, `expected a ${phase} event; got ${names.join(", ")}`).toContain(phase);
  }
  expect(names.filter((name) => name === "step.finished").length).toBeGreaterThan(0);

  const outcome = events.at(-1)!.data.payload as Record<string, any>;

  expect(
    outcome.status,
    `the run failed: ${outcome.error}. If this says no market data adapter is ` +
      "configured, an old e2e backend is being reused — stop it and rerun.",
  ).not.toBe("failed");

  // --- what the pipeline decided ---------------------------------------

  const planSteps = outcome.execution.outcomes as {
    step_id: string;
    tool: string;
    status: string;
    result: { estimates: unknown[]; diagnostics: unknown[] } | null;
  }[];
  console.log(
    `[gate] model=${model} status=${outcome.status} ` +
      `plan=${planSteps.map((s) => `${s.tool}:${s.status}`).join(" ")} ` +
      `verdict=${outcome.verdict?.approved} published=${outcome.narration?.published}`,
  );

  expect(outcome.plan.steps.length).toBeGreaterThan(0);
  // `results` is a computed property on the report, so it does not cross the
  // wire — `outcomes` is the contract, and a produced ResultSet is what
  // "ran end to end" has to mean.
  expect(planSteps.filter((step) => step.result !== null).length).toBeGreaterThan(0);
  expect(outcome.verdict).not.toBeNull();
  expect(outcome.narration).not.toBeNull();

  // The honesty seam: generated prices are declared, at risk severity.
  const flags = outcome.quality.flags as { code: string; severity: string }[];
  expect(flags.map((flag) => flag.code)).toContain("synthetic_data");
  expect(flags.find((flag) => flag.code === "synthetic_data")!.severity).toBe("risk");

  // Planner and Validator share a provider here, so the run must say so.
  expect(outcome.warnings.join(" ")).toContain("blind spots");

  // Every number in published prose is one the tools computed — that is what
  // publishing means. A withheld draft is the gate working, not a failure.
  //
  // There are two ways a draft is withheld and this used to assert only one of
  // them, so the gate passed or failed on the model's mood: `unusable_draft`
  // means no draft ever reached the grounding gate, and there are then no
  // issues to report. The assertion is on the *reason*, which the backend now
  // records, and the run annotates which path it took.
  if (outcome.narration.published) {
    expect(outcome.narration.withheld_reason).toBe("");
    expect(outcome.narration.grounding.grounded).toBe(true);
    expect(outcome.narration.narrative.prose.length).toBeGreaterThan(0);
  } else {
    expect(["ungrounded", "unusable_draft"]).toContain(outcome.narration.withheld_reason);
    if (outcome.narration.withheld_reason === "ungrounded") {
      expect(outcome.narration.grounding.issues.length).toBeGreaterThan(0);
    } else {
      // Nothing parsed, so there is nothing for the gate to have found. The
      // draft still cost tokens, and a withheld narration that recorded no
      // attempt would mean the Narrator was never asked.
      expect(outcome.narration.completions.length).toBeGreaterThan(0);
    }
    test.info().annotations.push({
      type: "note",
      description: `the narration was withheld: ${outcome.narration.withheld_reason}`,
    });
  }

  // --- the trace, read back from Postgres -------------------------------

  const runs = await (await request.get(`/api/chats/${chat.id}/runs`)).json();
  expect(runs).toHaveLength(1);
  expect(runs[0].status).toBe(outcome.status);
  expect(runs[0].tier).toBe("critic");

  const trace = await (await request.get(`/api/runs/${runs[0].id}`)).json();
  const steps = trace.steps as TraceStep[];

  expect(steps.length).toBeGreaterThan(3);
  expect(new Set(steps.map((step) => step.agent))).toEqual(
    new Set(["planner", "data_steward", "econometrician", "validator", "narrator"]),
  );

  // Only the first step is a root; every later one names an earlier step.
  const seen = new Set<string>();
  for (const [index, step] of steps.entries()) {
    if (index === 0) expect(step.parent_id).toBeNull();
    else expect(seen.has(step.parent_id ?? "")).toBe(true);
    seen.add(step.id);
  }

  // The model calls are attributed and costed; the tool calls are hashed.
  const llm = steps.filter((step) => step.kind === "llm");
  expect(llm.every((step) => step.provider === "ollama" && step.model === model)).toBe(true);
  expect(llm.some((step) => step.output_tokens > 0)).toBe(true);

  const tools = steps.filter((step) => step.tool !== null);
  expect(tools.length).toBeGreaterThan(0);
  expect(tools.every((step) => (step.tool_call_hash ?? "").length === 64)).toBe(true);

  expect(runs[0].output_tokens).toBe(
    steps.reduce((total, step) => total + step.output_tokens, 0),
  );
});

test("a project with no model assigned refuses before spending anything", async ({ request }) => {
  const project = await (
    await request.post("/api/projects", { data: { name: `${projectName} bare` } })
  ).json();
  const chat = await (
    await request.post(`/api/projects/${project.id}/chats`, { data: { name: "run" } })
  ).json();

  const response = await request.post(`/api/chats/${chat.id}/runs`, {
    data: { question: QUESTION },
  });

  expect(response.status()).toBe(503);
  expect((await response.json()).detail).toContain("planner");

  // Nothing was recorded, because nothing ran.
  expect(await (await request.get(`/api/chats/${chat.id}/runs`)).json()).toHaveLength(0);
});
