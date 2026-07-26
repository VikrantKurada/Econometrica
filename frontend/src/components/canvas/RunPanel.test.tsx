import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelInfo, ProviderStatus } from "../../lib/types";
import { renderWithProviders } from "../../test/renderWithProviders";
import { FIXTURE_RUN } from "./fixtures";
import { RunPanel } from "./RunPanel";

vi.mock("../charts/plotly", () => ({
  default: { react: vi.fn(), purge: vi.fn(), register: vi.fn() },
}));

const PROVIDERS: ProviderStatus[] = [
  {
    name: "ollama",
    label: "Ollama",
    requires_key: false,
    key_url: "",
    configured: true,
    reachable: true,
    detail: "",
    models_available: 1,
  },
];

const MODELS: ModelInfo[] = [
  {
    id: "ministral-3:8b",
    name: "ministral-3:8b",
    capabilities: {
      tool_calling: true,
      json_mode: true,
      streaming: true,
      vision: false,
      context_window: 32768,
    },
  },
];

function sse(events: { event: string; data: unknown }[]): string {
  return (
    events.map((e) => `event: ${e.event}\r\ndata: ${JSON.stringify(e.data)}`).join("\r\n\r\n") +
    "\r\n\r\n"
  );
}

let runs: unknown[] = [];
let patched: unknown = null;
let runBody = "";
let startStatus = 200;
let startFailure = { detail: "" };

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";

      if (url === "/api/providers") return Response.json(PROVIDERS);
      if (url === "/api/providers/ollama/models") return Response.json(MODELS);
      if (url.startsWith("/api/projects/") && method === "PATCH") {
        patched = JSON.parse(String(init?.body));
        return Response.json({ id: "p1" });
      }
      if (url.endsWith("/runs") && method === "POST") {
        if (startStatus !== 200) {
          return new Response(JSON.stringify(startFailure), { status: startStatus });
        }
        return new Response(new TextEncoder().encode(runBody), {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      if (url.endsWith("/runs")) return Response.json(runs);
      if (url.startsWith("/api/runs/")) return Response.json(FIXTURE_RUN);
      return new Response("not found", { status: 404 });
    }),
  );
}

beforeEach(() => {
  runs = [];
  patched = null;
  runBody = sse([{ event: "run.finished", data: { payload: FIXTURE_RUN.outcome } }]);
  startStatus = 200;
  startFailure = { detail: "" };
  installFetch();
});

afterEach(() => vi.unstubAllGlobals());

async function askAndRun(user: ReturnType<typeof renderWithProviders>["user"]) {
  await user.type(await screen.findByLabelText("Question"), "Does AAA follow a random walk?");
  const model = await screen.findByLabelText("Analysis model");
  await waitFor(() => expect(within(model).getAllByRole("option").length).toBeGreaterThan(1));
  await user.selectOptions(model, "ministral-3:8b");
  await user.click(screen.getByRole("button", { name: "Run analysis" }));
}

describe("RunPanel", () => {
  it("cannot start without a question and a model", async () => {
    const { user } = renderWithProviders(<RunPanel chatId="c1" projectId="p1" />);

    expect(await screen.findByRole("button", { name: "Run analysis" })).toBeDisabled();

    await user.type(await screen.findByLabelText("Question"), "why?");
    const model = await screen.findByLabelText("Analysis model");
    await waitFor(() => expect(within(model).getAllByRole("option").length).toBeGreaterThan(1));
    await user.selectOptions(model, "ministral-3:8b");

    expect(screen.getByRole("button", { name: "Run analysis" })).toBeEnabled();
  });

  it("assigns the roles before starting, because a run refuses without them", async () => {
    // `POST /runs` binds roles from Project.model_assignments and 503s when a
    // required one is missing, so the picker's choice has to be written first.
    const { user } = renderWithProviders(<RunPanel chatId="c1" projectId="p1" />);
    await askAndRun(user);

    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toMatchObject({
      model_assignments: {
        planner: { provider: "ollama", model: "ministral-3:8b" },
        narrator: { provider: "ollama", model: "ministral-3:8b" },
      },
    });
  });

  it("shows the phases as they arrive", async () => {
    runBody = sse([
      { event: "run.started", data: { detail: "q" } },
      { event: "plan.finished", data: {} },
      { event: "step.finished", data: { detail: "s1 (garch): ran" } },
      { event: "run.finished", data: { payload: FIXTURE_RUN.outcome } },
    ]);

    const { user } = renderWithProviders(<RunPanel chatId="c1" projectId="p1" />);
    await askAndRun(user);

    expect(await screen.findByText(/s1 \(garch\): ran/)).toBeInTheDocument();
  });

  it("shows the canvas for the finished run", async () => {
    runs = [{ ...FIXTURE_RUN, id: FIXTURE_RUN.id }];

    renderWithProviders(<RunPanel chatId="c1" projectId="p1" />);

    expect(await screen.findByRole("tab", { name: "Trace" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/generated, not observed/);
  });

  it("surfaces a refusal to start rather than hanging", async () => {
    startStatus = 503;
    startFailure = { detail: "ollama has no api key configured" };

    const { user } = renderWithProviders(<RunPanel chatId="c1" projectId="p1" />);
    await askAndRun(user);

    expect(await screen.findByRole("alert")).toHaveTextContent(/no api key configured/);
  });
});
