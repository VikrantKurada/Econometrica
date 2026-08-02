import type {
  Capabilities,
  Chat,
  ChatCreate,
  ChatUpdate,
  ColumnRole,
  Dataset,
  Health,
  Metrics,
  Message,
  ModelInfo,
  Project,
  ProjectCreate,
  ProjectUpdate,
  ProviderStatus,
  RerunReport,
  Run,
  RunDetail,
  Upload,
} from "./types";

/**
 * Every request goes through the Vite dev proxy at /api, so there is no base
 * URL to configure and the backend never needs CORS.
 */
const BASE = "/api";

/** A non-2xx response, carrying enough detail to render something useful. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

interface FastApiValidationIssue {
  loc?: unknown[];
  msg?: string;
}

function isValidationIssue(value: unknown): value is FastApiValidationIssue {
  return typeof value === "object" && value !== null && "msg" in value;
}

/**
 * FastAPI reports validation failures as a list of issues keyed by body path.
 * Flatten them into one sentence a person can act on — "name: must not be
 * blank" rather than a silent failure or a raw JSON dump.
 */
export function formatApiError(status: number, body: unknown): string {
  const detail =
    typeof body === "object" && body !== null && "detail" in body
      ? (body as { detail: unknown }).detail
      : undefined;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const parts = detail.filter(isValidationIssue).map((issue) => {
      // loc is ["body", "name"]; the field is the last hop that is not "body".
      const field = Array.isArray(issue.loc)
        ? issue.loc.filter((hop) => hop !== "body" && typeof hop === "string").at(-1)
        : undefined;
      const message = (issue.msg ?? "is invalid").replace(/^Value error,\s*/, "");
      return field ? `${String(field)}: ${message}` : message;
    });
    if (parts.length > 0) return parts.join("; ");
  }

  return `Request failed (${status})`;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

/**
 * The single place the app touches the network.
 *
 * `body` is serialised verbatim — explicit `null`s survive, which is what makes
 * the three-state chat capability toggles expressible over the wire.
 */
export async function request<T>(
  path: string,
  init: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const { method = "GET", body, signal } = init;

  const response = await fetch(`${BASE}${path}`, {
    method,
    signal,
    headers: body === undefined ? { Accept: "application/json" } : {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (!response.ok) {
    const errorBody = await readBody(response);
    throw new ApiError(response.status, formatApiError(response.status, errorBody), errorBody);
  }

  if (response.status === 204) return undefined as T;
  return (await readBody(response)) as T;
}

export const api = {
  health: (): Promise<Health> => request<Health>("/health"),

  listProjects: (): Promise<Project[]> => request<Project[]>("/projects"),

  createProject: (payload: ProjectCreate): Promise<Project> =>
    request<Project>("/projects", { method: "POST", body: payload }),

  /**
   * Partial by construction: whatever keys `payload` carries are the only ones
   * sent, and the backend's `exclude_unset` leaves the rest alone.
   */
  updateProject: (projectId: string, payload: ProjectUpdate): Promise<Project> =>
    request<Project>(`/projects/${projectId}`, { method: "PATCH", body: payload }),

  deleteProject: (projectId: string): Promise<void> =>
    request<void>(`/projects/${projectId}`, { method: "DELETE" }),

  /**
   * Multipart, so it bypasses `request` — that helper always sends JSON, and
   * setting Content-Type by hand on a FormData body drops the boundary and the
   * server sees an unparseable request.
   */
  uploadFile: async (projectId: string, file: File): Promise<Upload> => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${BASE}/projects/${projectId}/uploads`, {
      method: "POST",
      body: form,
    });
    if (!response.ok) {
      const errorBody = await readBody(response);
      throw new ApiError(response.status, formatApiError(response.status, errorBody), errorBody);
    }
    return (await readBody(response)) as Upload;
  },

  metrics: (): Promise<Metrics> => request<Metrics>("/metrics"),

  readUpload: (uploadId: string): Promise<Upload> =>
    request<Upload>(`/uploads/${uploadId}`),

  confirmUpload: (uploadId: string, roles: Record<string, ColumnRole>): Promise<Upload> =>
    request<Upload>(`/uploads/${uploadId}/confirm`, { method: "POST", body: { roles } }),

  listDatasets: (projectId: string): Promise<Dataset[]> =>
    request<Dataset[]>(`/projects/${projectId}/datasets`),

  listChats: (projectId: string): Promise<Chat[]> =>
    request<Chat[]>(`/projects/${projectId}/chats`),

  createChat: (projectId: string, payload: ChatCreate): Promise<Chat> =>
    request<Chat>(`/projects/${projectId}/chats`, { method: "POST", body: payload }),

  /**
   * `payload.web_search_enabled` has three meanings and all three survive the
   * trip: absent leaves the override alone, `null` clears it back to inherited,
   * and a boolean sets it explicitly.
   */
  updateChat: (chatId: string, payload: ChatUpdate): Promise<Chat> =>
    request<Chat>(`/chats/${chatId}`, { method: "PATCH", body: payload }),

  deleteChat: (chatId: string): Promise<void> =>
    request<void>(`/chats/${chatId}`, { method: "DELETE" }),

  chatCapabilities: (chatId: string): Promise<Capabilities> =>
    request<Capabilities>(`/chats/${chatId}/capabilities`),

  /** The transcript, already ordered by the server's sequence key. */
  listMessages: (chatId: string): Promise<Message[]> =>
    request<Message[]>(`/chats/${chatId}/messages`),

  /** Most recent first, and without the outcome — see `RunDetail` for why. */
  listRuns: (chatId: string): Promise<Run[]> => request<Run[]>(`/chats/${chatId}/runs`),

  /** One run with its steps and everything it produced. */
  getRun: (runId: string): Promise<RunDetail> => request<RunDetail>(`/runs/${runId}`),

  /**
   * Re-execute a recorded plan and ask whether it still produces the same
   * numbers. No model is consulted, so this is about the data and the tools.
   */
  rerunRun: (runId: string): Promise<RerunReport> =>
    request<RerunReport>(`/runs/${runId}/rerun`, { method: "POST" }),

  listProviders: (): Promise<ProviderStatus[]> => request<ProviderStatus[]>("/providers"),

  listProviderModels: (provider: string): Promise<ModelInfo[]> =>
    request<ModelInfo[]>(`/providers/${provider}/models`),

  setProviderKey: (provider: string, apiKey: string): Promise<ProviderStatus> =>
    request<ProviderStatus>(`/providers/${provider}/key`, {
      method: "PUT",
      body: { api_key: apiKey },
    }),

  deleteProviderKey: (provider: string): Promise<void> =>
    request<void>(`/providers/${provider}/key`, { method: "DELETE" }),
};

// Sending a message is not here: it streams, so it needs the byte-level reader
// in `streamChat` rather than this JSON-shaped helper.
