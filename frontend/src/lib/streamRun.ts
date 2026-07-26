import { formatApiError } from "./api";
import { parseSseChunk } from "./streamChat";
import type { RunOutcome } from "./types";

/**
 * The multi-agent pipeline, streamed.
 *
 * A separate reader from `streamChat` because the two speak different
 * vocabularies: chat streams tokens, a run streams *phases* — plan, data,
 * step, charts, validate, narrate — over minutes rather than seconds. The SSE
 * framing is shared; what the events mean is not.
 *
 * Phase names are treated as opaque strings rather than narrowed to a union.
 * The backend adds phases as the pipeline grows (`charts.finished` is one),
 * and a client that threw on an unfamiliar name would break on a deployment
 * that added a step.
 */

export interface RunPhase {
  name: string;
  detail: string;
  payload: Record<string, unknown>;
}

export interface StreamRunHandlers {
  onPhase?: (phase: RunPhase) => void;
  /** A finding about the analysis, not progress — surfaced, never scrolled past. */
  onWarning?: (detail: string) => void;
  onFinished?: (outcome: RunOutcome) => void;
  onError?: (detail: string) => void;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}

export interface RunStart {
  question: string;
  context?: string;
}

export async function streamRun(
  chatId: string,
  body: RunStart,
  handlers: StreamRunHandlers = {},
): Promise<void> {
  const { onPhase, onWarning, onFinished, onError, signal, fetchImpl = fetch } = handlers;

  let response: Response;
  try {
    response = await fetchImpl(`/api/chats/${chatId}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (isAbort(error)) return;
    onError?.(error instanceof Error ? error.message : String(error));
    return;
  }

  if (!response.ok) {
    // Refusals arrive before the stream starts and as ordinary JSON: no model
    // assigned, no API key, unknown chat.
    let parsed: unknown;
    try {
      parsed = JSON.parse(await response.text()) as unknown;
    } catch {
      parsed = undefined;
    }
    onError?.(formatApiError(response.status, parsed));
    return;
  }

  const reader = response.body?.getReader();
  if (!reader) {
    onError?.("the server sent no response body");
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const { events, rest } = parseSseChunk(buffer);
      buffer = rest;

      for (const { event, data } of events) {
        const body = (data ?? {}) as { detail?: unknown; payload?: unknown };
        const detail = String(body.detail ?? "");
        const payload = (body.payload ?? {}) as Record<string, unknown>;

        if (event === "run.finished") {
          onFinished?.(payload as unknown as RunOutcome);
        } else if (event === "run.warning") {
          onWarning?.(detail);
        } else if (event === "run.failed") {
          onError?.(detail || "the run failed");
        } else if (event === "run.untraced") {
          // The run happened and was watched; only the write failed. Reporting
          // it as a failed run would be a lie about the analysis.
          onError?.(detail);
        } else {
          onPhase?.({ name: event, detail, payload });
        }
      }
    }
  } catch (error) {
    if (!isAbort(error)) {
      onError?.(error instanceof Error ? error.message : String(error));
    }
  } finally {
    reader.releaseLock?.();
  }
}

function isAbort(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}
