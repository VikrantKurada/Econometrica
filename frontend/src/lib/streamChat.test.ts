import { describe, expect, it, vi } from "vitest";

import { parseSseChunk, streamChat } from "./streamChat";
import type { Message } from "./types";

function sse(events: { event: string; data: unknown }[], sep = "\r\n\r\n"): string {
  return events.map((e) => `event: ${e.event}\r\ndata: ${JSON.stringify(e.data)}`).join(sep) + sep;
}

function assistant(content: string): Message {
  return {
    id: "m2",
    chat_id: "c1",
    seq: 2,
    role: "assistant",
    content,
    provider: "ollama",
    model: "llama3.2:latest",
    input_tokens: 26,
    output_tokens: 8,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    latency_ms: 412,
    stop_reason: "stop",
    error: null,
    created_at: "2026-07-25T00:00:00Z",
  };
}

/** Feed a body to streamChat one arbitrary slice at a time. */
function fetchReturning(body: string, slices = 1): typeof fetch {
  return vi.fn(async () => {
    const size = Math.ceil(body.length / slices);
    const parts = Array.from({ length: slices }, (_, i) =>
      body.slice(i * size, (i + 1) * size),
    );
    const encoder = new TextEncoder();
    return new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          for (const part of parts) controller.enqueue(encoder.encode(part));
          controller.close();
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );
  }) as unknown as typeof fetch;
}

describe("parseSseChunk", () => {
  it("decodes a complete event", () => {
    const { events, rest } = parseSseChunk('event: delta\r\ndata: {"text":"hi"}\r\n\r\n');
    expect(events).toEqual([{ event: "delta", data: { text: "hi" } }]);
    expect(rest).toBe("");
  });

  it("holds back a partial event until its terminator arrives", () => {
    // A TCP read can split anywhere; emitting early would drop half a token.
    const first = parseSseChunk('event: delta\r\ndata: {"text":"he');
    expect(first.events).toEqual([]);

    const second = parseSseChunk(first.rest + 'llo"}\r\n\r\n');
    expect(second.events).toEqual([{ event: "delta", data: { text: "hello" } }]);
  });

  it("decodes several events from one chunk", () => {
    const { events } = parseSseChunk(
      sse([
        { event: "delta", data: { text: "a" } },
        { event: "delta", data: { text: "b" } },
      ]),
    );
    expect(events.map((e) => (e.data as { text: string }).text)).toEqual(["a", "b"]);
  });

  it("accepts bare newline separators as well as CRLF", () => {
    const { events } = parseSseChunk('event: delta\ndata: {"text":"hi"}\n\n');
    expect(events).toEqual([{ event: "delta", data: { text: "hi" } }]);
  });

  it("skips an event whose data is not JSON rather than throwing", () => {
    const { events } = parseSseChunk("event: delta\r\ndata: not-json\r\n\r\n");
    expect(events).toEqual([]);
  });

  it("ignores comment keep-alives", () => {
    const { events } = parseSseChunk(": keep-alive\r\n\r\n");
    expect(events).toEqual([]);
  });
});

describe("streamChat", () => {
  const body = { content: "hi", provider: "ollama", model: "llama3.2:latest" };

  it("reports deltas in order and then the finished message", async () => {
    const deltas: string[] = [];
    let done: Message | undefined;

    await streamChat("c1", body, {
      onDelta: (text) => deltas.push(text),
      onDone: (message) => {
        done = message;
      },
      fetchImpl: fetchReturning(
        sse([
          { event: "start", data: { user_message: { id: "m1" } } },
          { event: "delta", data: { text: "The beta " } },
          { event: "delta", data: { text: "is 1.3." } },
          { event: "done", data: { message: assistant("The beta is 1.3.") } },
        ]),
      ),
    });

    expect(deltas).toEqual(["The beta ", "is 1.3."]);
    expect(done?.content).toBe("The beta is 1.3.");
  });

  it("reassembles correctly when the body is split mid-event", async () => {
    // The realistic case: chunk boundaries are wherever the network puts them.
    const deltas: string[] = [];
    await streamChat("c1", body, {
      onDelta: (t) => deltas.push(t),
      fetchImpl: fetchReturning(
        sse([
          { event: "delta", data: { text: "one" } },
          { event: "delta", data: { text: "two" } },
          { event: "delta", data: { text: "three" } },
        ]),
        17,
      ),
    });
    expect(deltas.join("")).toBe("onetwothree");
  });

  it("hands the persisted user message to onStart", async () => {
    let started: unknown;
    await streamChat("c1", body, {
      onStart: (message) => {
        started = message;
      },
      fetchImpl: fetchReturning(
        sse([{ event: "start", data: { user_message: { id: "m1", content: "hi" } } }]),
      ),
    });
    expect(started).toMatchObject({ id: "m1", content: "hi" });
  });

  it("surfaces a server error event", async () => {
    let error: string | undefined;
    await streamChat("c1", body, {
      onError: (detail) => {
        error = detail;
      },
      fetchImpl: fetchReturning(
        sse([{ event: "error", data: { detail: "ollama: daemon not running" } }]),
      ),
    });
    expect(error).toBe("ollama: daemon not running");
  });

  it("surfaces a non-2xx response as an error rather than a silent no-op", async () => {
    let error: string | undefined;
    const failing = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "OpenAI has no api key configured" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
    ) as unknown as typeof fetch;

    await streamChat("c1", body, {
      onError: (detail) => {
        error = detail;
      },
      fetchImpl: failing,
    });

    expect(error).toContain("no api key");
  });

  it("stops cleanly when aborted and reports no error", async () => {
    // Aborting is the user pressing Stop, not a failure.
    const controller = new AbortController();
    const errors: string[] = [];

    const fetchImpl = vi.fn(async () => {
      controller.abort();
      const err = new DOMException("aborted", "AbortError");
      throw err;
    }) as unknown as typeof fetch;

    await streamChat("c1", body, {
      signal: controller.signal,
      onError: (d) => errors.push(d),
      fetchImpl,
    });

    expect(errors).toEqual([]);
  });

  it("posts to the chat's message endpoint", async () => {
    const fetchImpl = fetchReturning(sse([]));
    await streamChat("c1", body, { fetchImpl });

    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe("/api/chats/c1/messages");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});
