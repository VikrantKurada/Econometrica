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
