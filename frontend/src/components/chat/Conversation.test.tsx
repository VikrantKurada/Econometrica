import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Message, ModelInfo, ProviderStatus } from "../../lib/types";
import { renderWithProviders } from "../../test/renderWithProviders";
import { Conversation } from "./Conversation";

const PROVIDERS: ProviderStatus[] = [
  {
    name: "ollama",
    label: "Ollama",
    requires_key: false,
    key_url: "",
    configured: true,
    reachable: true,
    detail: "",
    models_available: 2,
  },
  {
    name: "openai",
    label: "OpenAI",
    requires_key: true,
    key_url: "https://example.test",
    configured: false,
    reachable: false,
    detail: "no api key configured for OpenAI",
    models_available: 0,
  },
];

const MODELS: ModelInfo[] = [
  {
    id: "llama3.2:latest",
    name: "llama3.2:latest",
    capabilities: {
      tool_calling: true,
      json_mode: true,
      streaming: true,
      vision: false,
      context_window: 8192,
    },
  },
  {
    id: "nomic-embed-text:latest",
    name: "nomic-embed-text:latest",
    capabilities: {
      tool_calling: false,
      json_mode: false,
      streaming: false,
      vision: false,
      context_window: 8192,
    },
  },
];

function message(overrides: Partial<Message>): Message {
  return {
    id: "m1",
    chat_id: "c1",
    seq: 1,
    role: "user",
    content: "hello",
    provider: null,
    model: null,
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    latency_ms: 0,
    stop_reason: null,
    error: null,
    created_at: "2026-07-25T00:00:00Z",
    ...overrides,
  };
}

function sseBody(events: { event: string; data: unknown }[]): string {
  return (
    events.map((e) => `event: ${e.event}\r\ndata: ${JSON.stringify(e.data)}`).join("\r\n\r\n") +
    "\r\n\r\n"
  );
}

function streamResponse(body: string): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        // Held open when a test needs to observe the half-streamed state that
        // a stream closing in the same tick would never let it see.
        if (holdStream) streamController = controller;
        else controller.close();
      },
    }),
    { status: 200, headers: { "Content-Type": "text/event-stream" } },
  );
}

let transcript: Message[] = [];
let streamBody = "";
let sent: unknown = null;
let holdStream = false;
let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url === "/api/providers") return Response.json(PROVIDERS);
      if (url === "/api/providers/ollama/models") return Response.json(MODELS);
      if (url.endsWith("/messages") && init?.method === "POST") {
        sent = JSON.parse(String(init.body));
        return streamResponse(streamBody);
      }
      if (url.endsWith("/messages")) return Response.json(transcript);
      return new Response("not found", { status: 404 });
    }),
  );
}

beforeEach(() => {
  transcript = [];
  streamBody = "";
  sent = null;
  holdStream = false;
  streamController = null;
  installFetch();
});

afterEach(() => {
  streamController?.close();
  streamController = null;
  vi.unstubAllGlobals();
});

async function pickModel(user: ReturnType<typeof renderWithProviders>["user"]) {
  const model = await screen.findByLabelText("Model");
  await waitFor(() => expect(within(model).getAllByRole("option").length).toBeGreaterThan(1));
  await user.selectOptions(model, "llama3.2:latest");
}

describe("Conversation", () => {
  it("renders an existing transcript in order", async () => {
    transcript = [
      message({ id: "m1", seq: 1, role: "user", content: "what is the beta?" }),
      message({ id: "m2", seq: 2, role: "assistant", content: "The beta is 1.3." }),
    ];

    renderWithProviders(<Conversation chatId="c1" />);

    const messages = await screen.findAllByRole("article");
    expect(messages).toHaveLength(2);
    expect(messages[0]).toHaveAccessibleName("user message");
    expect(messages[1]).toHaveAccessibleName("assistant message");
  });

  it("shows provenance on an assistant turn", async () => {
    transcript = [
      message({
        id: "m2",
        seq: 2,
        role: "assistant",
        content: "hi",
        provider: "ollama",
        model: "llama3.2:latest",
        input_tokens: 26,
        output_tokens: 8,
        latency_ms: 4391,
      }),
    ];

    renderWithProviders(<Conversation chatId="c1" />);

    const provenance = await screen.findByTestId("provenance");
    expect(provenance).toHaveTextContent("llama3.2:latest");
    expect(provenance).toHaveTextContent("26 in / 8 out");
    expect(provenance).toHaveTextContent("4.4s");
  });

  it("renders a failed turn as an error rather than dropping it", async () => {
    transcript = [
      message({
        id: "m2",
        seq: 2,
        role: "assistant",
        content: "",
        error: "ollama: daemon not running",
      }),
    ];

    renderWithProviders(<Conversation chatId="c1" />);

    expect(await screen.findByText(/daemon not running/)).toBeInTheDocument();
  });

  it("offers only reachable providers", async () => {
    renderWithProviders(<Conversation chatId="c1" />);

    const provider = await screen.findByLabelText("Provider");
    const options = within(provider).getAllByRole("option").map((o) => o.textContent);
    expect(options).toContain("Ollama");
    expect(options).not.toContain("OpenAI");
  });

  it("hides models that cannot hold a conversation", async () => {
    // An Ollama install is usually full of embedding models; offering one
    // produces a failure that looks like an app bug.
    renderWithProviders(<Conversation chatId="c1" />);

    const model = await screen.findByLabelText("Model");
    await waitFor(() => expect(within(model).getAllByRole("option").length).toBeGreaterThan(1));
    const options = within(model).getAllByRole("option").map((o) => o.textContent);
    expect(options).toContain("llama3.2:latest");
    expect(options).not.toContain("nomic-embed-text:latest");
  });

  it("cannot send until a model is chosen", async () => {
    const { user } = renderWithProviders(<Conversation chatId="c1" />);

    await user.type(await screen.findByLabelText("Message"), "hello");
    expect(screen.getByLabelText("Send message")).toBeDisabled();

    await pickModel(user);
    expect(screen.getByLabelText("Send message")).toBeEnabled();
  });

  it("cannot send an empty message", async () => {
    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    expect(screen.getByLabelText("Send message")).toBeDisabled();
  });

  it("streams a reply and then shows the persisted transcript", async () => {
    streamBody = sseBody([
      { event: "start", data: { user_message: message({ id: "m1", content: "beta?" }) } },
      { event: "delta", data: { text: "The beta " } },
      { event: "delta", data: { text: "is 1.3." } },
      {
        event: "done",
        data: {
          message: message({ id: "m2", seq: 2, role: "assistant", content: "The beta is 1.3." }),
        },
      },
    ]);

    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    await user.type(await screen.findByLabelText("Message"), "beta?");

    transcript = [
      message({ id: "m1", seq: 1, content: "beta?" }),
      message({ id: "m2", seq: 2, role: "assistant", content: "The beta is 1.3." }),
    ];
    await user.click(screen.getByLabelText("Send message"));

    expect(await screen.findByText("The beta is 1.3.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(2));
  });

  it("does not label the placeholder with provenance before the first token", async () => {
    // The placeholder carries the chosen model so the badge can render the
    // moment the turn is stored — but until then there is nothing to attest
    // to, and a badge under an empty bubble claims a reply that has not
    // happened. Provenance means "this turn is persisted", nothing less.
    holdStream = true;
    streamBody = sseBody([
      { event: "start", data: { user_message: message({ id: "m1", content: "beta?" }) } },
    ]);

    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    await user.type(await screen.findByLabelText("Message"), "beta?");
    await user.click(screen.getByLabelText("Send message"));

    expect(await screen.findByLabelText("Thinking")).toBeInTheDocument();
    expect(screen.queryByTestId("provenance")).toBeNull();
  });

  it("sends the chosen provider and model with the message", async () => {
    streamBody = sseBody([{ event: "done", data: { message: message({ id: "m2" }) } }]);

    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    await user.type(await screen.findByLabelText("Message"), "beta?");
    await user.click(screen.getByLabelText("Send message"));

    await waitFor(() =>
      expect(sent).toEqual({
        content: "beta?",
        provider: "ollama",
        model: "llama3.2:latest",
      }),
    );
  });

  it("clears the composer once the message is sent", async () => {
    streamBody = sseBody([{ event: "done", data: { message: message({ id: "m2" }) } }]);

    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    const box = await screen.findByLabelText("Message");
    await user.type(box, "beta?");
    await user.click(screen.getByLabelText("Send message"));

    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("surfaces a stream error without losing the transcript", async () => {
    streamBody = sseBody([
      { event: "start", data: { user_message: message({ id: "m1", content: "beta?" }) } },
      { event: "error", data: { detail: "ollama: daemon not running" } },
    ]);
    transcript = [message({ id: "m1", seq: 1, content: "beta?" })];

    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    await user.type(await screen.findByLabelText("Message"), "beta?");
    await user.click(screen.getByLabelText("Send message"));

    expect(await screen.findByRole("alert")).toHaveTextContent("daemon not running");
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(1));
  });

  it("sends on Enter but not on Shift+Enter", async () => {
    streamBody = sseBody([{ event: "done", data: { message: message({ id: "m2" }) } }]);

    const { user } = renderWithProviders(<Conversation chatId="c1" />);
    await pickModel(user);
    const box = await screen.findByLabelText("Message");

    await user.type(box, "line one{Shift>}{Enter}{/Shift}");
    expect(sent).toBeNull();
    expect(box).toHaveValue("line one\n");

    await user.type(box, "{Enter}");
    await waitFor(() => expect(sent).not.toBeNull());
  });

  it("does not render markup from model output", async () => {
    // Model output is untrusted; the one thing it must not do is inject markup.
    transcript = [
      message({
        id: "m2",
        seq: 2,
        role: "assistant",
        content: "<img src=x onerror=alert(1)>",
      }),
    ];

    const { container } = renderWithProviders(<Conversation chatId="c1" />);

    await screen.findByRole("article");
    expect(container.querySelector("img")).toBeNull();
  });

  it("renders markdown from model output", async () => {
    transcript = [
      message({ id: "m2", seq: 2, role: "assistant", content: "the **beta** is 1.3" }),
    ];

    renderWithProviders(<Conversation chatId="c1" />);

    expect(await screen.findByText("beta")).toContainHTML("beta");
    expect((await screen.findByRole("article")).querySelector("strong")).not.toBeNull();
  });
});
