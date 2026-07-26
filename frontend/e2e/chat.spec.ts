import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

/**
 * Phase 3 gate: a real prompt, to a real local model, streamed back over the
 * real SSE endpoint and persisted in the real database.
 *
 * Nothing here is mocked. That is the point — the unit tests already prove the
 * SSE parser, the Ollama adapter and the chat pane in isolation, and each of
 * those proofs rests on a belief about what the other side sends. This is the
 * only test that puts a browser, the Vite proxy, FastAPI, the Ollama daemon
 * and Postgres on the same wire at once.
 *
 * Two things are asserted that a smoke test would not:
 *
 * 1. **The reply streamed.** `fetch` is wrapped before the page loads so the
 *    SSE body can be tee'd and recorded read by read. Asserting on the
 *    rendered text alone would pass just as happily against an endpoint that
 *    buffered the whole answer and sent it in one frame.
 * 2. **The reply is the server's.** After a reload the transcript is refetched
 *    from Postgres, so what is on screen afterwards cannot be a client-side
 *    reconstruction.
 */

const stamp = Date.now();
const projectName = `E2E chat project ${stamp}`;
const chatName = `E2E chat ${stamp}`;

const PROMPT = "In one short sentence, what is a random walk?";

/**
 * Ollama serves whatever this machine happens to have pulled, in tag order
 * rather than size order, and the largest local models are over 40 GB — taking
 * the first option would turn a gate into a load test. Prefer a small chat
 * model, allow an explicit override through `E2E_OLLAMA_MODEL`, and fall back
 * to anything that streams so the gate still runs on a different library.
 */
const SMALL_CHAT_MODELS = [
  "tinyllama",
  "llama3.2:1b",
  "qwen2.5:0.5b",
  "qwen3:0.6b",
  "smollm2",
  "gemma3:1b",
  "phi3:mini",
];

interface ProviderStatus {
  name: string;
  reachable: boolean;
}

interface ModelInfo {
  id: string;
  capabilities: { streaming: boolean };
}

/** One network read of the SSE body, with the moment it landed. */
interface SseChunk {
  at: number;
  text: string;
}

interface SseRecording {
  chunks: SseChunk[];
  /** The stream reached EOF. Until then the recording is still filling. */
  done: boolean;
  error: string | null;
}

declare global {
  interface Window {
    __econSse?: SseRecording;
  }
}

/**
 * The model this run should use, or null if Ollama cannot answer at all.
 *
 * Asked over the API rather than read off the `<select>`: the test has to tell
 * "Ollama is down" (skip, with a reason) apart from "Ollama is up but the
 * picker is empty" (a real failure), and the rendered options cannot.
 */
async function pickOllamaModel(request: APIRequestContext): Promise<string | null> {
  const providersResponse = await request.get("/api/providers");
  if (!providersResponse.ok()) return null;

  const providers = (await providersResponse.json()) as ProviderStatus[];
  if (!providers.find((provider) => provider.name === "ollama")?.reachable) return null;

  const modelsResponse = await request.get("/api/providers/ollama/models");
  if (!modelsResponse.ok()) return null;

  const models = (await modelsResponse.json()) as ModelInfo[];
  const chatModels = models.filter((model) => model.capabilities.streaming);
  if (chatModels.length === 0) return null;

  const override = process.env.E2E_OLLAMA_MODEL;
  if (override) {
    const match = chatModels.find((model) => model.id === override);
    if (!match) throw new Error(`E2E_OLLAMA_MODEL=${override} is not a streaming Ollama model`);
    return match.id;
  }

  const small = chatModels.find((model) =>
    SMALL_CHAT_MODELS.some((name) => model.id.startsWith(name)),
  );
  return (small ?? chatModels[0])!.id;
}

/**
 * Record the SSE body as the browser receives it.
 *
 * `body.tee()` hands the app one branch and the test the other, so the reply
 * is observed without being consumed. Installed as an init script because the
 * wrapper has to be in place before the app's module graph captures `fetch`.
 *
 * Playwright's own `response.text()` is not an option here: on a server-sent
 * event stream it fails with "No data found for resource with given
 * identifier", because nothing buffers a body that was consumed as it arrived.
 */
async function recordSseChunks(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const recording: SseRecording = { chunks: [], done: false, error: null };
    window.__econSse = recording;

    const original = window.fetch;

    window.fetch = async (input, init) => {
      const response = await original(input, init);

      const url = input instanceof Request ? input.url : String(input);
      const method = (input instanceof Request ? input.method : init?.method) ?? "GET";
      // Only the send endpoint streams; the GET on the same path is plain JSON.
      if (method.toUpperCase() !== "POST" || !url.includes("/messages") || !response.body) {
        return response;
      }

      const [forApp, forTest] = response.body.tee();

      void (async () => {
        const reader = forTest.getReader();
        const decoder = new TextDecoder();
        try {
          for (;;) {
            const { done, value } = await reader.read();
            if (done) break;
            recording.chunks.push({
              at: performance.now(),
              text: decoder.decode(value, { stream: true }),
            });
          }
        } catch (cause) {
          recording.error = String(cause);
        }
        recording.done = true;
      })();

      return new Response(forApp, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    };
  });
}

/**
 * The recording, once the stream has ended.
 *
 * The wait is load-bearing rather than defensive: the app's branch of the tee
 * is drained by the browser's own response consumer, so the UI can finish
 * rendering the reply while the test's branch still has buffered reads to work
 * through. Sampling without waiting sees a truncated stream.
 */
async function sseRecording(page: Page): Promise<SseRecording> {
  await page.waitForFunction(() => window.__econSse?.done === true, undefined, {
    timeout: 30_000,
  });
  return page.evaluate(() => window.__econSse!);
}

/** Delete every project this run created, whatever state the test died in. */
async function cleanUp(request: APIRequestContext): Promise<void> {
  const response = await request.get("/api/projects");
  if (!response.ok()) return;
  const projects = (await response.json()) as { id: string; name: string }[];
  for (const project of projects) {
    if (project.name.includes(String(stamp))) {
      await request.delete(`/api/projects/${project.id}`);
    }
  }
}

test.afterEach(async ({ request }) => {
  await cleanUp(request);
});

test("a message to a local model streams back and survives a reload", async ({
  page,
  request,
}) => {
  // A cold Ollama loads the model off disk before the first token, and the
  // fallback model may be a large one. Well beyond the 60s default.
  test.setTimeout(240_000);

  const model = await pickOllamaModel(request);
  if (model === null) {
    test.skip(
      true,
      "Ollama is unreachable or serves no chat model — start it with `ollama serve` " +
        "and pull one (`ollama pull tinyllama`) to run the Phase 3 gate.",
    );
    return;
  }

  await recordSseChunks(page);
  await page.goto("/");

  // A project and a chat, created the way a person would. Creation drops
  // straight into inline naming, so create and name are one gesture.
  await page.getByRole("button", { name: "New project" }).click();
  const projectInput = page.getByRole("textbox", { name: /rename project/i });
  await projectInput.fill(projectName);
  await projectInput.press("Enter");
  await expect(page.getByRole("treeitem", { name: projectName })).toBeVisible();

  await page.getByText(projectName, { exact: true }).hover();
  await page.getByRole("button", { name: `New chat in ${projectName}` }).click();
  const chatInput = page.getByRole("textbox", { name: /rename chat/i });
  await chatInput.fill(chatName);
  await chatInput.press("Enter");
  await expect(page.getByRole("treeitem", { name: chatName })).toBeVisible();

  // Creating a chat selects it, so the composer is live from here on.
  const transcript = page.getByTestId("transcript");
  await expect(transcript).toBeVisible();

  // Exact, because the canvas pane carries its own "Analysis model" and
  // "Validator model" pickers and getByLabel matches on substring — the same
  // hazard the Message locator below has always had.
  await page.getByLabel("Provider").selectOption("ollama");
  await page.getByLabel("Model", { exact: true }).selectOption(model);

  // By role, not by label: "Message" is a substring of "Send message", so a
  // bare label lookup matches the send button too.
  await page.getByRole("textbox", { name: "Message", exact: true }).fill(PROMPT);
  await page.getByRole("button", { name: "Send message" }).click();

  // The user's turn appears from the server's `start` event, not optimistically.
  const userMessage = transcript.getByRole("article", { name: "user message" });
  await expect(userMessage).toContainText(PROMPT);

  // Provenance renders only from a message the server has already persisted,
  // so its arrival is the signal that the turn is complete and stored.
  const provenance = transcript.getByTestId("provenance");
  await expect(provenance).toBeVisible({ timeout: 180_000 });
  await expect(provenance).toContainText(model);

  const reply = (
    await transcript
      .getByRole("article", { name: "assistant message" })
      .getByTestId("message-body")
      .innerText()
  ).trim();
  expect(reply.length).toBeGreaterThan(0);

  // What actually came down the wire. A buffered reply would arrive as one
  // frame in one read; a streamed one is a frame per token across many reads.
  const recording = await sseRecording(page);
  const body = recording.chunks.map((chunk) => chunk.text).join("");
  const deltas = body.split("event: delta").length - 1;

  expect(recording.error).toBeNull();
  expect(body).toContain("event: start");
  expect(body).toContain("event: done");
  expect(body).not.toContain("event: error");
  expect(deltas).toBeGreaterThan(1);
  expect(recording.chunks.length).toBeGreaterThan(1);
  // Spread over time rather than delivered at one instant.
  expect(recording.chunks.at(-1)!.at).toBeGreaterThan(recording.chunks[0]!.at);

  // The point of the exercise: the transcript is Postgres', not the client's.
  await page.reload();

  await expect(userMessage).toContainText(PROMPT);
  await expect(
    transcript.getByRole("article", { name: "assistant message" }).getByTestId("message-body"),
  ).toHaveText(reply);
  await expect(transcript.getByTestId("provenance")).toContainText(model);
});
