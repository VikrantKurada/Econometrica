/** Wire types for the Econometrica API. UUIDs and timestamps arrive as strings. */

import type { ChartSpec } from "../components/charts/spec";

export interface Health {
  status: string;
  version: string;
}

export type ValidationTier = "single" | "critic" | "consensus";

export interface Project {
  id: string;
  name: string;
  description: string | null;
  web_search_enabled: boolean;
  mcp_enabled: boolean;
  code_sandbox_enabled: boolean;
  validation_tier: ValidationTier;
  model_assignments: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/**
 * A chat's capability toggles are three-state, and the third state is the point:
 *
 * - `true` / `false` — an explicit override on this chat
 * - `null`           — no override; inherit whatever the project says
 *
 * `null` is a value here, never a stand-in for "unknown", so it must never be
 * coerced with `?? false` or `Boolean(...)`. Resolving inheritance is the
 * backend's job, exposed at GET /api/chats/{id}/capabilities.
 */
export interface Chat {
  id: string;
  project_id: string;
  name: string;
  web_search_enabled: boolean | null;
  mcp_enabled: boolean | null;
  created_at: string;
  updated_at: string;
}

/** What a chat can actually do once project settings and overrides are merged. */
export interface Capabilities {
  web_search: boolean;
  mcp: boolean;
  code_sandbox: boolean;
  validation_tier: ValidationTier;
}

export interface ProjectCreate {
  name: string;
  description?: string | null;
}

/**
 * A PATCH body. Every field is optional and only what is present is sent, which
 * is what keeps the request a partial update rather than a full replacement.
 */
export interface ProjectUpdate {
  name?: string;
  description?: string | null;
  web_search_enabled?: boolean;
  mcp_enabled?: boolean;
  code_sandbox_enabled?: boolean;
  validation_tier?: ValidationTier;
  model_assignments?: Record<string, unknown>;
}

export interface ChatCreate {
  name: string;
}

/**
 * Note the difference between an absent key and a `null` value: omitting
 * `web_search_enabled` leaves the override as it is, while sending `null`
 * clears it and hands the decision back to the project.
 */
export interface ChatUpdate {
  name?: string;
  web_search_enabled?: boolean | null;
  mcp_enabled?: boolean | null;
}

export type MessageRole = "system" | "user" | "assistant";

/**
 * One turn. Assistant turns carry their own provenance — which provider and
 * model produced them and what they cost — because the model can change
 * between turns, so the chat alone could not explain any individual reply.
 *
 * `error` and useful `content` are mutually exclusive: a failed generation is
 * persisted with an empty body so the failure stays visible in the transcript
 * rather than the turn silently vanishing.
 */
export interface Message {
  id: string;
  chat_id: string;
  /** Server-assigned ordering key. Sort on this, never on `created_at`. */
  seq: number;
  role: MessageRole;
  content: string;
  provider: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  latency_ms: number;
  stop_reason: string | null;
  error: string | null;
  created_at: string;
}

export interface MessageSend {
  content: string;
  provider: string;
  model: string;
}

export interface ProviderStatus {
  name: string;
  label: string;
  requires_key: boolean;
  key_url: string;
  /** Has whatever it needs to be used — a key, where one is required. */
  configured: boolean;
  /** Answered its health probe. Only probed when configured. */
  reachable: boolean;
  detail: string;
  models_available: number;
}

/* --- econometric results ------------------------------------------------- */
/* Mirrors `backend/src/econometrica/econ/types.py`. Every number the UI shows
   comes from one of these: a tool computed it, and the manifest reproduces it. */

export interface Estimate {
  name: string;
  value: number;
  std_error: number | null;
  t_stat: number | null;
  p_value: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

export interface Diagnostic {
  name: string;
  statistic: number;
  p_value: number | null;
  critical_values: Record<string, number>;
  /** Tri-state. `null` means the tool did not judge it, never that it failed. */
  passed: boolean | null;
  interpretation: string;
}

export interface Table {
  columns: string[];
  rows: unknown[][];
}

/**
 * `x` is whatever the tool indexed on — dates as ISO strings, lags, horizons.
 * The Python side types it `list[Any]`; over JSON it can only arrive as one of
 * these, and narrowing it here is what lets a chart plot it without a cast.
 */
export interface Series {
  name: string;
  x: (string | number | null)[];
  y: (number | null)[];
}

export interface Manifest {
  data_fingerprint: string;
  tool: string;
  tool_version: string;
  params_hash: string;
  library_versions: Record<string, string>;
  seed: number | null;
  created_at: string;
}

export interface ResultSet {
  tool: string;
  version: string;
  params: Record<string, unknown>;
  estimates: Estimate[];
  diagnostics: Diagnostic[];
  scalars: Record<string, number>;
  tables: Record<string, Table>;
  series: Record<string, Series>;
  manifest: Manifest;
}

/* --- runs ---------------------------------------------------------------- */
/* Mirrors the agent pipeline's own models. Note what is *not* here: several
   backend conveniences (`ExecutionReport.refusals`, `PreconditionVerdict.
   refused`) are Python properties, so they never reach the wire. The canvas
   derives them, and `refusals.ts` is the one place that knows the rule. */

export type FlagSeverity = "info" | "warning" | "risk";

export interface QualityFlag {
  code: string;
  severity: FlagSeverity;
  detail: string;
}

export interface DataQualityReport {
  tickers: string[];
  frequency: string;
  return_method: string;
  /** Which adapter the prices came from — part of reproducing a number. */
  source: string;
  rows: number;
  start: string;
  end: string;
  dropped_rows: number;
  fingerprint: string;
  flags: QualityFlag[];
}

/** One gate's answer. `judged: false` means the check could not be evaluated. */
export interface PreconditionVerdict {
  tool: string;
  check: string;
  allowed: boolean;
  judged: boolean;
  detail: string;
}

export type StepStatus = "ran" | "refused" | "failed" | "skipped";

export interface StepOutcome {
  step_id: string;
  tool: string;
  status: StepStatus;
  result: ResultSet | null;
  verdicts: PreconditionVerdict[];
  error: string;
}

export interface ExecutionReport {
  outcomes: StepOutcome[];
}

export interface PlanStep {
  id: string;
  tool: string;
  params: Record<string, unknown>;
  depends_on: string[];
  rationale: string;
}

/**
 * A calculation no registry tool performs.
 *
 * Empty in every plan unless the project has the code sandbox enabled — the
 * Planner is only told the field exists when it is.
 */
export interface CodeStep {
  id: string;
  intent: string;
  depends_on: string[];
  rationale: string;
}

export interface AnalysisPlan {
  question: string;
  dataset: {
    tickers: string[];
    start: string;
    end: string;
    frequency: string;
    return_method: string;
    risk_free: string | null;
  };
  steps: PlanStep[];
  code_steps: CodeStep[];
  hypotheses: string[];
  chart_intents: string[];
}

export interface ValidationVerdict {
  approved: boolean;
  reasons: string[];
  revise_steps: string[];
}

export interface GroundingIssue {
  value: number;
  text: string;
  sentence: string;
}

export interface GroundingReport {
  grounded: boolean;
  issues: GroundingIssue[];
  checked: number;
}

/**
 * Why an interpretation was withheld. Empty when it was published.
 *
 * `ungrounded` — the gate found figures nothing computed.
 * `unusable_draft` — no draft ever reached the gate: it would not parse, or it
 * cited a step the plan never had. Saying "cited numbers no result supports"
 * in that case is a false statement about what the model did.
 */
export type WithheldReason = "" | "ungrounded" | "unusable_draft";

export interface Narration {
  published: boolean;
  narrative: { prose: string; citations: string[] } | null;
  withheld_reason: WithheldReason;
  grounding: GroundingReport;
}

export type RunStatus = "running" | "completed" | "blocked" | "failed";

/** Everything a run produced, however far it got. */
export interface RunOutcome {
  status: RunStatus;
  question: string;
  plan: AnalysisPlan | null;
  quality: DataQualityReport | null;
  execution: ExecutionReport | null;
  verdict: ValidationVerdict | null;
  narration: Narration | null;
  charts: ChartSpec[];
  diagnostics: Diagnostic[];
  warnings: string[];
  revisions: number;
  error: string;
}

export interface RunStep {
  id: string;
  seq: number;
  parent_id: string | null;
  agent: string;
  kind: string;
  status: string;
  attempt: number;
  provider: string | null;
  model: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  tool: string | null;
  tool_call_hash: string | null;
  detail: string;
  /** What this attempt was sent and what came back. Empty on tool steps. */
  prompt: string;
  response: string;
  created_at: string;
}

export interface Run {
  id: string;
  chat_id: string;
  question: string;
  status: RunStatus;
  tier: ValidationTier;
  revisions: number;
  error: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_ms: number;
  created_at: string;
}

/**
 * One run with its steps and everything it produced. `outcome` is `{}` for a
 * run recorded before the column existed, so it is narrowed rather than
 * assumed.
 */
export interface RunDetail extends Run {
  steps: RunStep[];
  outcome: Partial<RunOutcome>;
}

export interface StepReproduction {
  step_id: string;
  tool: string;
  reproduced: boolean;
  status: string;
  original_status: string;
  data_fingerprint: string;
  original_data_fingerprint: string;
  params_hash: string;
  original_params_hash: string;
  detail: string;
}

export interface RerunReport {
  run_id: string;
  reproduced: boolean;
  steps: StepReproduction[];
}

export interface ModelCapabilities {
  tool_calling: boolean;
  json_mode: boolean;
  streaming: boolean;
  vision: boolean;
  context_window: number;
}

export interface ModelInfo {
  id: string;
  name: string;
  capabilities: ModelCapabilities;
}

// --- uploads ----------------------------------------------------------------

export type ColumnRole =
  | "date"
  | "ticker"
  | "price"
  | "return"
  | "volume"
  | "factor"
  | "ignore";

export interface RoleCandidate {
  role: ColumnRole;
  score: number;
  reason: string;
}

export interface ColumnProfile {
  name: string;
  dtype: "number" | "datetime" | "text" | "boolean";
  present: number;
  missing: number;
  unique: number;
  minimum: number | null;
  maximum: number | null;
  sample: string[];
  parses_as_date: boolean;
  decimal_comma: boolean;
  candidates: RoleCandidate[];
}

export interface FileProfile {
  filename: string;
  format: "csv" | "xlsx" | "parquet";
  rows: number;
  columns: ColumnProfile[];
  layout: "wide" | "long" | "unknown";
  delimiter: string | null;
}

export interface MappingProposal {
  roles: Record<string, ColumnRole>;
  rationale: Record<string, string>;
  /** Columns where more than one role genuinely fitted. */
  ambiguous: string[];
}

export interface ColumnMapping {
  roles: Record<string, ColumnRole>;
  confirmed: boolean;
}

export interface Upload {
  id: string;
  project_id: string;
  filename: string;
  profile: FileProfile;
  proposal: MappingProposal;
  consulted_model: boolean;
  confirmed: boolean;
  mapping: ColumnMapping | null;
  /** Present only on a confirmation response. */
  observations: number | null;
  symbols: string[];
  fields: string[];
}

export interface Dataset {
  id: string;
  project_id: string;
  name: string;
  source_label: string;
  rows: number;
  column_roles: Record<string, string>;
  fingerprint: string;
  created_at: string;
  symbols: string[];
}

// --- telemetry ---------------------------------------------------------------

export interface SpanMetric {
  name: string;
  count: number;
  p50: number;
  p95: number;
  p99: number;
  error_rate: number;
}

export interface TokenTotals {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

export interface TokensBy extends TokenTotals {
  key: string;
  cost_usd: number;
}

export interface Metrics {
  spans: SpanMetric[];
  tokens: TokenTotals;
  tokens_by_provider: TokensBy[];
  tokens_by_agent: TokensBy[];
  cost_usd: number;
  runs: number;
  revisions_total: number;
  revisions_mean: number | null;
  /** Null where nothing of that kind has run — not the same as zero. */
  tool_error_rate: number | null;
  validator_rejection_rate: number | null;
}
