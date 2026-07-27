import type { Metrics, RunStep } from "../../lib/types";

/**
 * A realistic run for the dev gallery: a planner retry nested under its first
 * attempt, a refused precondition, and a tool step with nothing to expand — the
 * three shapes the flat table could not tell apart.
 *
 * Not part of the app; `vite build` takes only index.html.
 */
function step(overrides: Partial<RunStep>): RunStep {
  return {
    id: "s",
    seq: 0,
    parent_id: null,
    agent: "planner",
    kind: "llm",
    status: "ok",
    attempt: 1,
    provider: "ollama",
    model: "ministral-3:8b",
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    latency_ms: 0,
    tool: null,
    tool_call_hash: null,
    detail: "",
    prompt: "",
    response: "",
    created_at: "2026-07-27T09:00:00Z",
    ...overrides,
  };
}

export const TRACE_FIXTURE: RunStep[] = [
  step({
    id: "a",
    seq: 1,
    status: "failed",
    detail: "reply rejected; retried",
    input_tokens: 4210,
    output_tokens: 180,
    latency_ms: 3100,
    prompt:
      "[system]\nYou are the Planner in an econometrics workbench.\n\n[user]\nIs Bitcoin a random walk?",
    response: "Sure! Here is the plan:\n\n```json\n{ tools: [adf] }\n```",
  }),
  step({
    id: "b",
    seq: 2,
    parent_id: "a",
    attempt: 2,
    input_tokens: 4580,
    output_tokens: 240,
    cost_usd: 0.0121,
    latency_ms: 2750,
    prompt:
      "[system]\nYou are the Planner…\n\n[assistant]\nSure! Here is the plan:\n\n[user]\nThat reply could not be used.",
    response: '{"question": "Is Bitcoin a random walk?", "steps": [{"id": "s1", "tool": "adf"}]}',
  }),
  step({
    id: "c",
    seq: 3,
    parent_id: "b",
    agent: "econometrician",
    kind: "tool",
    tool: "adf",
    provider: null,
    model: null,
    latency_ms: 84,
  }),
  step({
    id: "d",
    seq: 4,
    parent_id: "b",
    agent: "econometrician",
    kind: "tool",
    tool: "garch",
    status: "refused",
    provider: null,
    model: null,
    detail: "ARCH-LM finds no effects to model",
    latency_ms: 41,
  }),
  step({
    id: "e",
    seq: 5,
    parent_id: "b",
    agent: "narrator",
    input_tokens: 1980,
    output_tokens: 420,
    cost_usd: 0.0074,
    latency_ms: 5200,
    prompt: "[system]\nYou are the Narrator…\n\n[user]\n# Results\n\n## s1 — adf (ran)",
    response:
      '{"prose": "The ADF statistic is -15.065457 (s1), so the unit root is rejected.", "citations": ["s1"]}',
  }),
];

export const METRICS_FIXTURE: Metrics = {
  spans: [
    { name: "GET /api/runs/{run_id}", count: 128, p50: 12, p95: 48, p99: 96, error_rate: 0 },
    { name: "POST /api/chats/{chat_id}/runs", count: 9, p50: 18400, p95: 26100, p99: 26100, error_rate: 0.11 },
    { name: "GET /api/projects", count: 340, p50: 3, p95: 9, p99: 22, error_rate: 0 },
  ],
  tokens: { input: 84_200, output: 13_100, cache_read: 0, cache_write: 0 },
  tokens_by_provider: [
    { key: "anthropic", input: 12_400, output: 2_100, cache_read: 0, cache_write: 0, cost_usd: 0.184 },
    { key: "ollama", input: 71_800, output: 11_000, cache_read: 0, cache_write: 0, cost_usd: 0 },
  ],
  tokens_by_agent: [
    { key: "narrator", input: 19_800, output: 4_200, cache_read: 0, cache_write: 0, cost_usd: 0.074 },
    { key: "planner", input: 46_000, output: 6_800, cache_read: 0, cache_write: 0, cost_usd: 0.062 },
    { key: "validator", input: 18_400, output: 2_100, cache_read: 0, cache_write: 0, cost_usd: 0.048 },
  ],
  cost_usd: 0.184,
  runs: 9,
  revisions_total: 4,
  revisions_mean: 0.44,
  tool_error_rate: 0.08,
  validator_rejection_rate: null,
};
