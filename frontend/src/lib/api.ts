import type { Health } from "./types";

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
};
