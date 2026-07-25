/** Wire types for the Econometrica API. UUIDs and timestamps arrive as strings. */

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
