import { describe, expect, it, vi } from "vitest";

import { streamRun, type RunPhase } from "./streamRun";

function sse(events: { event: string; data: unknown }[]): string {
  return (
    events.map((e) => `event: ${e.event}\r\ndata: ${JSON.stringify(e.data)}`).join("\r\n\r\n") +
    "\r\n\r\n"
  );
}

function respondWith(body: string): typeof fetch {
  return vi.fn(async () => {
    const encoder = new TextEncoder();
    return new Response(
      new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(body));
          controller.close();
        },
      }),
      { status: 200, headers: { "Content-Type": "text/event-stream" } },
    );
  }) as unknown as typeof fetch;
}

const FINISHED = {
  event: "run.finished",
  data: { payload: { status: "completed", question: "q", charts: [] } },
};

describe("streamRun", () => {
  it("reports each phase as it happens", async () => {
    // The canvas shows progress through a run that takes minutes. Without the
    // phases it would sit blank until the terminal event arrived.
    const phases: RunPhase[] = [];
    await streamRun(
      "c1",
      { question: "q" },
      {
        onPhase: (phase) => phases.push(phase),
        fetchImpl: respondWith(
          sse([
            { event: "run.started", data: { detail: "q" } },
            { event: "plan.finished", data: { payload: { steps: [] } } },
            { event: "step.finished", data: { detail: "s1 (adf): ran" } },
            { event: "charts.finished", data: { detail: "2 chart(s)" } },
            FINISHED,
          ]),
        ),
      },
    );

    expect(phases.map((p) => p.name)).toEqual([
      "run.started",
      "plan.finished",
      "step.finished",
      "charts.finished",
    ]);
    expect(phases[2].detail).toBe("s1 (adf): ran");
  });

  it("hands back the finished outcome", async () => {
    const done = vi.fn();
    await streamRun(
      "c1",
      { question: "q" },
      { onFinished: done, fetchImpl: respondWith(sse([FINISHED])) },
    );

    expect(done).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed", question: "q" }),
    );
  });

  it("surfaces a warning as a warning, not as a phase", async () => {
    // A run.warning is a finding about the analysis — independence of the
    // validator, synthetic prices — and must not scroll past as progress.
    const warnings: string[] = [];
    await streamRun(
      "c1",
      { question: "q" },
      {
        onWarning: (text) => warnings.push(text),
        fetchImpl: respondWith(
          sse([
            { event: "run.warning", data: { detail: "planner and validator share a provider" } },
            FINISHED,
          ]),
        ),
      },
    );

    expect(warnings).toEqual(["planner and validator share a provider"]);
  });

  it("reports a run that was recorded but not traced", async () => {
    // The run happened and the client watched it; only the write failed. It
    // must not be reported as a failed run.
    const errors: string[] = [];
    await streamRun(
      "c1",
      { question: "q" },
      {
        onError: (detail) => errors.push(detail),
        fetchImpl: respondWith(
          sse([FINISHED, { event: "run.untraced", data: { detail: "disk full" } }]),
        ),
      },
    );

    expect(errors).toEqual(["disk full"]);
  });

  it("reports a refusal that arrives as JSON rather than as a stream", async () => {
    // Starting a run with no model assigned is a 503 with a plain body.
    const errors: string[] = [];
    const fetchImpl = vi.fn(
      async () =>
        new Response(JSON.stringify({ detail: "no model is assigned to the planner role" }), {
          status: 503,
        }),
    ) as unknown as typeof fetch;

    await streamRun("c1", { question: "q" }, { onError: (d) => errors.push(d), fetchImpl });

    expect(errors).toEqual(["no model is assigned to the planner role"]);
  });
});
