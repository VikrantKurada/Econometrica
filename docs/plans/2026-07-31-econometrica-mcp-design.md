# Econometrica — MCP tools, as a research phase before planning

*Design note, 2026-07-31. Read `CLAUDE.md` first; this closes the last item it
records as "still imported only by its own tests" — `mcp/` "has no schema for
`project.mcp_servers` and nothing that decides *when* a tool gets called."*

---

## What is already here, and what is not

The security surface is built and proven; nothing that would let a model *use* it
is.

**Present.**

- `mcp/client.py` — `McpClient(session, server, allowlist)`: `discover()` lists a
  server's tools each marked `allowed`, `call(tool, args)` gates on the allowlist
  **before the server is asked** and returns an `McpCall`. `McpCall.to_step_record`
  makes it a trace step. Tested against a **real** in-memory `FastMCP` server —
  the proof an unlisted tool never ran is the server's own execution log.
- `mcp/allowlist.py` — `Allowlist`/`ToolRef`: default-deny, exact match,
  server-qualified, no wildcards, read per call. `Allowlist.for_project(project)`.
- The LLM layer **already speaks tool-calling**: `complete(messages, tools=[ToolSpec])`
  returns `Completion.tool_calls`; `Message.tool_result(id, content)` and
  `Message.assistant(content, tool_calls)` carry the loop; `Capabilities.tool_calling`
  says whether a model can do it at all. `ToolSpec`'s field names already match
  what a tool schema needs.

**Missing — confirmed:** `McpClient`/`Allowlist` are imported only by their own
tests, and no API route touches MCP beyond the untyped `mcp_servers`/`mcp_allowlist`
fields on `ProjectUpdate`.

1. **No connection layer.** `McpClient.session` is handed in by the tests; nothing
   in the app builds a live session from a server configuration.
2. **No typed server schema.** `project.mcp_servers` is `list[Any]` — there is no
   shape to connect *from*, and no discovery route to help a user build the
   allowlist.
3. **No tool-calling loop.** Nothing offers the allowed tools to a model, runs
   what it chooses, feeds the results back, and iterates. **This is the real
   work** — search and retrieval are things the system *does* to build context;
   an MCP call is something a model *chooses*.
4. **No decision about which agent may call, and when.**

---

## The framing decision: MCP is context, never a number

An MCP tool's output is **read, not computed** — the same status web search and
retrieval have. It reaches the **Planner only**, never the Narrator, and the
grounding gate still admits only values a registry tool produced, so a figure a
model quotes from a tool's output is exactly as ungrounded as one it invented. A
test asserts it, beside the identical ones for web search and retrieval.

This is the choice that keeps the one invariant intact: **LLMs never compute
statistics; numbers come only from registry tools and the marked sandbox.** MCP
as a *computation* channel — a tool returning a price series that becomes a
`ResultSet` — was considered and deliberately deferred: it would need the
sandbox's whole "unvalidated, its own provenance, banner" apparatus, and it is a
separate, larger, security-heavier design. Here, MCP gathers *context*: a data
definition, an internal ticker universe, a methodology note, a document from a
company wiki — the kind of thing that stops a Planner inventing a symbol, which
is the same failure web search exists to reduce.

---

## The Research agent, and its loop

A new tool-using model role, **`researcher`**, runs a **bounded tool-calling
loop before planning** and produces a text summary appended to the Planner's
context. It is not an `Agent[T]` — that base is for a single validated JSON
reply — it is a free-form conversation that ends in prose.

The loop, reusing the LLM layer's existing tool transport:

1. **Offer only allowlisted tools.** The connector discovers every configured
   server's tools; the loop presents as `ToolSpec`s only those the project's
   allowlist permits. A tool the user has not allowed is never named to the
   model, so the model cannot reach for it.
2. **Run what the model chooses.** The model returns `tool_calls`. Each is
   executed through `McpClient.call`, which gates on the allowlist a second time
   — defence in depth, because a model can hallucinate a tool name. A refused
   call (not allowlisted) or an unreachable server becomes an **error
   `Message.tool_result` fed back to the model**, not a run failure: the model
   adapts or gives up, and the run continues either way.
3. **Feed results back and repeat.** Executed outputs return as
   `Message.tool_result`, and the loop asks the model again.
4. **Stop deliberately.** The loop ends when the model returns a turn with **no
   tool calls** (its text is the summary), or when it reaches **`max_rounds`**
   (~4 model calls). On the cap, one final call is made **with no tools offered**,
   asking the model to summarise what it found — so the Planner receives clean
   prose rather than a dangling tool-call turn.

The summary is appended as `# From MCP tools — read, not computed. Nothing here
is a result.`, the same shape web search and retrieval use.

**Why a bounded loop and not a single call.** A model that needs two facts
(discover a tool, then call it with the right argument) needs at least two
rounds; a hard cap stops a model that loops on one failing tool from spending the
budget. `max_rounds` is the retry policy — there is no separate "retry the whole
thing", because a research phase that found nothing is a run with less context,
not a failed run.

**The model must support tool-calling.** The orchestrator reads
`Capabilities.tool_calling` for the assigned model; a model that cannot call
tools skips the research phase rather than looping uselessly. Skipped, not
refused — MCP is context.

---

## The seam and the connection layer

`agents/` must not import `db.models` (the rule the whole codebase keeps), and it
must not import the MCP SDK's connection types either — those stop in `mcp/`, the
way a vendor SDK stops in `llm/providers/`. So the layering:

- **`mcp/config.py` (new, db-free).** `McpServerConfig` — a typed dataclass
  replacing the untyped `mcp_servers: list[Any]`:
  - `name: str`, `transport: "stdio" | "http"`;
  - stdio: `command: str`, `args: list[str]`, `env: dict[str, str]`;
  - http: `url: str`, `headers: dict[str, str]`.
- **`mcp/connect.py` (new, db-free).** Given an `McpServerConfig`, an async
  context manager yielding a connected `ClientSession` — over the SDK's stdio
  transport (`stdio_client`) or streamable-http transport
  (`streamablehttp_client`). And an **`McpConnector`** that opens *all* of a
  project's configured servers, yields their allowlist-gated `McpClient`s for the
  research phase, and closes every session on exit. Sessions live **only for the
  research phase**, opened per run and torn down after — an MCP server is not a
  long-running dependency of the app.
- **`agents/researcher.py` (new).** The `Researcher` runs the loop over the
  connector's clients and produces a `ResearchOutcome` (`summary`, the `McpCall`s
  it made, the model turns, `failed`/`detail`) with `to_step_record`-style
  helpers and `as_context()`, mirroring `SearchOutcome`. It imports `mcp/` and the
  LLM types; it imports no `db`.
- **`api/routers/runs.py`.** Translates the ORM `project.mcp_servers` (JSONB) into
  typed `McpServerConfig`s, builds the `McpConnector` and the `Researcher`, and
  passes the researcher into the orchestrator — exactly as it builds the searcher,
  query writer and retriever. The db lives here; `agents/` and `mcp/` stay off it.

The orchestrator gains `researcher: Researcher | None` and a
`_research_context(question, trace)` called before planning, alongside
`_retrieval_context` and `_search_context`. Order among the three: retrieval
(the project's own documents), then research (its MCP tools), then web search
(the open internet) — nearest-trust to furthest.

---

## The stdio trust model, stated plainly

A **stdio** MCP server is an **arbitrary local command spawned with the host's
privileges**. It is **not** sandboxed the way the quant-coder is — there is no
Job Object, no audit hook, no import allowlist around it. The MCP allowlist gates
which of the server's *tools* an agent may call; it does **not** constrain what
the spawned process can do once it is running.

So configuring a stdio server is **trusting that command**, the same act as
installing and running any local program. That trust is decided at configuration
time, by a person, per project. The design does not pretend otherwise, and the
docs will say it in these words: **use HTTP for a server you do not fully trust;
stdio is trust-the-command.**

The mitigations that do apply: a server is spawned only during the research
phase, per run, and its process is torn down when the phase ends — it is not a
resident daemon. Servers are project-scoped. And nothing is ever called that the
allowlist does not name. What is explicitly **out of scope** here: sandboxing the
server process (a large, separate effort), and secrets management for stdio `env`
beyond storing it in the project config (a follow-up — today it is config, like
`BRAVE_API_KEY` is a setting).

---

## Gating

MCP research runs when **all** of these hold, and is skipped (never refused)
otherwise:

- the resolved `mcp` capability is on (project setting a chat may override);
- at least one server is configured on the project;
- the allowlist is non-empty (default-deny means an empty one permits nothing, so
  there is nothing to offer);
- a `researcher` role is assigned in `model_assignments`;
- the assigned model reports `tool_calling`.

Any missing → no connector is built and the orchestrator receives
`researcher=None`, so a project that never configured MCP runs exactly as today.
A server that is unreachable at run time degrades the phase (an error result, or
an empty summary) rather than failing the run. This mirrors the misconfigured
query-writer and the failed search: MCP is context, and losing it is the smaller
loss.

Re-run is untouched — it re-executes a recorded plan without re-planning, and
research only ever shaped the plan's *context*.

---

## Trace and the migration

Every model turn in the loop is `agent="researcher"`, `kind="llm"`; every
`McpCall` is `agent="researcher"`, `kind="tool"`, `tool="mcp:<server>:<tool>"`.
`researcher` is a **new trace-agent value**, so — unlike retrieval — this feature
needs a **hand-written CHECK migration** widening `ck_run_steps_agent_known`, the
`quant_coder`/`query_writer` pattern, with the two gates that caught those (a real
insert against Postgres, and the constraint-value test).

`McpCall.to_step_record` today carries a placeholder `agent="econometrician"` —
it predates any decision about who drives MCP. It becomes `agent="researcher"`.
The existing client tests assert the step's `tool`, `kind`, `status` and `detail`,
not its agent, so they are unaffected.

---

## Discovery — how a user builds the allowlist

`GET /api/projects/{id}/mcp/tools` connects to each configured server and returns
every tool it offers, each with its description, input schema, and an `allowed`
flag from the current allowlist. This is how a person decides what to permit —
and **discovery is not permission**, a principle the client already keeps:
listing a tool marks it, it does not make it callable. The connection is live
(it spawns stdio servers / reaches http ones), so an unreachable server is
reported per server rather than failing the whole listing.

The allowlist itself is already settable through `PATCH /api/projects/{id}`
(`mcp_allowlist`), sent whole rather than patched — "an allowlist edited by delta
is one whose current contents the client has to have guessed right." The typed
`mcp_servers` replaces the untyped list on the same update.

---

## Testing, red first

- **`mcp/config.py`** — a config round-trips from the JSONB shape; an unknown
  transport is rejected; stdio without a command, http without a url, are
  rejected.
- **`mcp/connect.py`** — against the in-memory transport where possible; a
  `McpConnector` over two servers yields gated clients and closes them; an
  unreachable server degrades rather than raising the whole connect.
- **`agents/researcher.py`** — the loop against a `FakeProvider` scripting
  `tool_calls` and a real in-memory `FastMCP` server:
  - a model that calls an allowed tool gets its output and summarises it;
  - a model that calls a **non-allowlisted** tool gets an error result and the
    server never runs it (the named criterion, again);
  - the loop stops at `max_rounds` and still returns a summary;
  - a model that calls no tool returns its text in one round;
  - an unreachable server degrades to a failed outcome, not a raise.
- **`agents/test_orchestrator.py`** — a researcher's summary reaches the Planner
  and leaves `mcp:`/`researcher` trace steps; `researcher=None` → no research;
  a failed research degrades the run; research precedes the plan.
- **`db/test_run_model.py` / `test_migrations.py`** — a `researcher` step inserts;
  the value reaches a migration.
- **`api/test_runs.py`** — a project with MCP configured builds a researcher; one
  without does not (asserted on construction).
- **`api/test_documents.py`-style route test** — discovery lists a server's tools
  with `allowed` flags.
- **The grounding invariant** — a number in an MCP tool's output, quoted in a
  narration, is still blocked. The web-search/retrieval test, a third time.
- **A live probe** (`@pytest.mark.live`) — a real model over a real (in-memory or
  local) server, calling a tool and summarising, asserting the tool ran and the
  summary is marked read-not-computed. Skips when the model is absent.

---

## What this does not do

- **MCP as computation.** No tool output becomes a `ResultSet`. That needs the
  sandbox's provenance apparatus and is a separate design.
- **Sandboxing a stdio server.** The process runs with host privileges; trust is
  decided at config time. HTTP is the untrusted-server answer.
- **Secrets management.** stdio `env` and http `headers` are stored in the project
  config, like `BRAVE_API_KEY` is a setting. A keystore-backed path is a follow-up.
- **A config UI.** Backend-first, as uploads, retrieval and the column mapper all
  were.
