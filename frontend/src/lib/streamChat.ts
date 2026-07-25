import { formatApiError } from "./api";
import type { Message, MessageSend } from "./types";

/**
 * Streaming chat over POST.
 *
 * `EventSource` is the obvious tool for server-sent events and cannot be used
 * here: it only issues GET requests and cannot carry a JSON body. So this
 * reads the response as a byte stream and parses the SSE framing by hand.
 *
 * The parsing is deliberately incremental. A network read can split anywhere —
 * mid-word, mid-JSON, between the `event:` and `data:` lines — so anything
 * after the last event terminator is carried forward rather than parsed, and
 * emitting a half-received token is never possible.
 */

export interface SseEvent {
  event: string;
  data: unknown;
}

/**
 * Split a buffer into whole SSE events plus whatever trails the last complete
 * one. Feed `rest` back in front of the next chunk.
 */
export function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  // Events are terminated by a blank line; servers vary on CRLF vs LF.
  const parts = buffer.split(/\r?\n\r?\n/);
  // The final part is either empty (buffer ended on a terminator) or a
  // partially received event, so it is never parsed here.
  const rest = parts.pop() ?? "";

  for (const block of parts) {
    let name = "message";
    const dataLines: string[] = [];

    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(":")) continue; // keep-alive comment
      if (line.startsWith("event:")) name = line.slice("event:".length).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice("data:".length).trim());
    }

    if (dataLines.length === 0) continue;
    try {
      events.push({ event: name, data: JSON.parse(dataLines.join("\n")) as unknown });
    } catch {
      // A malformed event must not tear down an otherwise good stream.
    }
  }

  return { events, rest };
}

export interface StreamChatHandlers {
  onStart?: (userMessage: Message) => void;
  onDelta?: (text: string) => void;
  onDone?: (message: Message) => void;
  onError?: (detail: string) => void;
  signal?: AbortSignal;
  /** Injectable so tests can drive the reader without a network. */
  fetchImpl?: typeof fetch;
}

export async function streamChat(
  chatId: string,
  body: MessageSend,
  handlers: StreamChatHandlers = {},
): Promise<void> {
  const { onStart, onDelta, onDone, onError, signal, fetchImpl = fetch } = handlers;

  let response: Response;
  try {
    response = await fetchImpl(`/api/chats/${chatId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    // Aborting is the user pressing Stop, not a failure to report.
    if (isAbort(error)) return;
    onError?.(error instanceof Error ? error.message : String(error));
    return;
  }

  if (!response.ok) {
    // A refusal (no API key, unknown chat) arrives as ordinary JSON, not SSE.
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
        const payload = data as Record<string, unknown>;
        if (event === "start") onStart?.(payload.user_message as Message);
        else if (event === "delta") onDelta?.(String(payload.text ?? ""));
        else if (event === "done") onDone?.(payload.message as Message);
        else if (event === "error") onError?.(String(payload.detail ?? "generation failed"));
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
