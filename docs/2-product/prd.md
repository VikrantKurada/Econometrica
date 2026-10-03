# Product Requirements Document

- **The point:** the product is shipped. This page is written against what
  exists, with the open items marked as open. The functional requirements are
  listed with the acceptance criterion that was actually used.
- **Read time:** about 17 minutes end to end. It is a reference: jump by
  section number.
- **Do first:** read [3.1 Goals](#31-goals). Seven rows, each with how it is
  met.

| | |
|---|---|
| **Product** | Econometrica |
| **Version** | 1.0, covering phases 0 through 6 |
| **Status** | Shipped. Written against what exists, with the open items marked as open |
| **Last reviewed** | 2026-08-21 |

---

## 1. Summary

**Econometrica is a locally-run econometrics workbench for financial asset
pricing and market efficiency analysis.**

A user asks a question in prose. A multi-agent pipeline then:

1. plans an analysis
2. resolves the data
3. runs typed econometric tools
4. checks the assumptions
5. validates the work with a second model, meant to sit on a different vendor
6. writes an interpretation whose every number is verified against the
   computed results

The distinguishing constraint: **language models never compute statistics**.
They select from a registry of tested functions. This is not a performance
optimisation. It is the product.

## 2. The problem

**Financial analysts and researchers have two options today, and both are
bad.**

| Option | Speed | Trust | What goes wrong |
|---|---|---|---|
| **Do it by hand**: write the pandas, fit the model, make the chart, check the diagnostics, write it up | slow | correct | It does not scale across questions. Each new question is a new notebook, and six months later nobody can reproduce the third one |
| **Ask a language model** | fast | untrustworthy | The number is sometimes right, and nothing in the output separates the right ones from the wrong ones |

Letting the model write and run code does not rescue the second option. It
can produce a cleanly-executing, well-formatted, badly wrong answer. We
measured this: one run in five, at temperature zero, on a simple statistic.

There is no third option that is both fast and trustworthy. That is the gap.

### 2.1 What makes this hard

- **Assumption checks get skipped.** Correct econometrics needs them, most
  practitioners skip them, and no chat interface performs them.
- **Reproduction needs three things pinned:** the data, the tool version and
  the parameters. A result is only useful later if you can reproduce it, and a
  conversational interface records none of the three.
- **Market data is not stable.** The same vendor's split-adjusted and
  dividend-adjusted close for AAPL on 2020-08-25 differ by 3.1%. A number
  without its adjustment policy is not reproducible even in principle.

## 3. Goals and non-goals

### 3.1 Goals

| # | Goal | How it is met |
|---|---|---|
| G1 | Every number a user sees traces to a tested function | Registry of 37 typed tools; nothing else may produce a figure |
| G2 | A result can be reproduced later, or the difference explained | Manifest per result; `POST /api/runs/{id}/rerun` with a per-step report |
| G3 | Methods are not applied to data that cannot support them | Executable gates checked before a tool runs |
| G4 | An interpretation never contains an unverified number | Numeric grounding gate; withholds the whole narration |
| G5 | The user can see exactly which model decided what | Trace DAG per run: agent, provider, model, prompt, response, tokens, cost, latency, parents |
| G6 | It runs on one machine with no account and no cloud dependency | Local Postgres, local Ollama, everything else optional |
| G7 | Work can be taken away from the application | Five data export formats, chart images, PDF via print, all carrying the manifest |

### 3.2 Non-goals

| # | Non-goal | Why |
|---|---|---|
| N1 | Multi-user, authentication, permissions | Single user removes an entire tier of scope and keeps the local Ollama instance reachable. Revisit for the medium-term roadmap. |
| N2 | Being the fastest way to get a number | It is the most checkable way. Those are different products. |
| N3 | Novel econometric methods | The reference implementations are correct. The value is in selection, application and provenance. |
| N4 | Trading execution, order routing, portfolio management | Out of scope for v1. Discussed in the [roadmap](../5-roadmap/). |
| N5 | Investment advice of any kind | The application produces statistics and their interpretation. It does not recommend positions. |
| N6 | Server-side chart rendering | The fourteen renderers are TypeScript. A server render would export a picture nobody looked at. |

## 4. Users

**Four personas, in descending order of how much the product is shaped around
them.** A full session for each is in [user journeys](journeys.md).

| Persona | Who | Primary job | What they need most |
|---|---|---|---|
| **The quantitative analyst** | Buy or sell side, comfortable with statsmodels, short on time | "Is this factor loading real, and can I defend it in a meeting?" | Correct methods, diagnostics, an export that carries the manifest |
| **The researcher** | Academic or independent, writing something that will be reviewed | "Is this market weak-form efficient over this window?" | Reproducibility, the full method trail, refusals that teach |
| **The risk officer** | Reviews other people's work | "Where did this number come from and can I get it back?" | The trace DAG, the manifest, the re-run, provenance in the printout |
| **The learner** | Knows finance, learning econometrics | "Why can I not fit a GARCH to this?" | Gate refusals with reasons, tri-state diagnostics, the narrative |

## 5. Functional requirements

**Seven areas. Each requirement has an ID, a statement, and the acceptance
criterion that was actually used.**

| Area | Covers | Requirements |
|---|---|---|
| 5.1 | Projects, chats and capabilities | FR-1.1 to FR-1.6 |
| 5.2 | Data | FR-2.1 to FR-2.16 |
| 5.3 | The analysis run | FR-3.1 to FR-3.10 |
| 5.4 | Context channels | FR-4.1 to FR-4.12 |
| 5.5 | The code escape hatch | FR-5.1 to FR-5.5 |
| 5.6 | Output | FR-6.1 to FR-6.10 |
| 5.7 | Telemetry | FR-7.1 to FR-7.7 |

### 5.1 Projects, chats and capabilities

| ID | Requirement | Acceptance |
|---|---|---|
| FR-1.1 | A user can create, rename, list and delete projects and chats within them | CRUD endpoints; deleting a project cascades its chats |
| FR-1.2 | A project carries capability toggles: web search, MCP, code sandbox, and a validation tier | Columns on `projects`, all NOT NULL with matching server defaults |
| FR-1.3 | A chat may override its project's web search and MCP toggles; `null` means inherit | `resolve_capabilities` returns the project value when the chat value is `null` |
| FR-1.4 | The code sandbox toggle is **not** overridable per chat | It is the most security-sensitive toggle in the system, so it stays at project scope |
| FR-1.5 | Each agent role can be assigned its own provider and model per project | `Project.model_assignments`, a JSONB map of role to provider and model |
| FR-1.6 | A run refuses to start if a required role has no usable model | Validated before anything runs, for the same reason an unconfigured provider is refused before the user's turn is written |

### 5.2 Data

| ID | Requirement | Acceptance |
|---|---|---|
| FR-2.1 | Market prices are fetched from a configured source and cached on disk | `ECONOMETRICA_PRICE_SOURCE=yahoo`; a run, its re-run and its exports share one fetch |
| FR-2.2 | Every price source names its adjustment policy in its label, and the label reaches the quality report | Test per source |
| FR-2.3 | A cache entry expires (default one day) and, when stale **and** the source is unreachable, raises rather than serving | Staleness has no disclosure channel, so serving stale silently is refused |
| FR-2.4 | An analysis may request a risk-free rate from any of seventeen FRED series | Resolved into a per-period `risk_free` column, de-annualised by compounding |
| FR-2.5 | A spec asking for a risk-free rate with no rate source configured is **refused**, not run on raw returns | Excess and raw returns answer different questions and nothing downstream could tell |
| FR-2.6 | An analysis may request a Fama-French factor set (`ff3`, `ff5`, `carhart4`) | Joined onto the frame under the tools' own parameter names |
| FR-2.7 | A factor set brings its own risk-free column; also asking for a FRED rate raises `mixed_risk_free` | Honoured, but flagged |
| FR-2.8 | A user can upload CSV, XLSX or Parquet, see a profile with role candidates per column, and confirm a mapping | `POST /api/projects/{id}/uploads`, then `POST /api/uploads/{id}/confirm` |
| FR-2.9 | Only a person-confirmed mapping is ever ingested | `confirm_mapping` is the only producer of a mapping `apply_mapping` acts on |
| FR-2.10 | A model may only reorder role candidates the deterministic profiler already found admissible; a user may pick any role | The two are constrained differently on purpose |
| FR-2.11 | Ingested observations land in a Timescale hypertable and are served through the same `PriceSource` protocol as a market source | Nothing above the protocol knows uploads exist |
| FR-2.12 | A run reads the project's uploads first and falls through to the market source | So one frame can mix an uploaded index with a listed ticker |
| FR-2.13 | A run drawing on more than one source raises an **info**-severity `mixed_sources` flag naming every ticker under the source that served it | Info, not warning: mixing is the feature |
| FR-2.14 | A filename containing "synthetic" is refused at confirm time | It would otherwise reach the `synthetic_data` substring check. Refused rather than rewritten: quietly editing provenance is invisible |
| FR-2.15 | A synthetic-source run always carries a `synthetic_data` risk flag the canvas cannot hide | Alert above the tabs |
| FR-2.16 | With no source configured, a run refuses with an explanation | It does not invent data |

### 5.3 The analysis run

| ID | Requirement | Acceptance |
|---|---|---|
| FR-3.1 | `POST /api/chats/{id}/runs` streams progress as server-sent events | Dotted event names, not a discriminated union, so a new phase does not break an old client |
| FR-3.2 | A plan is a typed `AnalysisPlan`: dataset spec, steps bound to the registry, hypotheses, chart intents | A step naming an unknown tool, carrying an undeclared parameter, or forming a cycle is rejected at the boundary |
| FR-3.3 | Unknown parameters are rejected rather than ignored | An invented `confidence` would vanish silently and the user would believe the level was honoured |
| FR-3.4 | Three validation tiers: `single`, `critic` (default), `consensus` | Deterministic gates run in **every** tier. Cheap must not mean unguarded |
| FR-3.5 | A rejection buys exactly one revision | Unbounded, a Validator and a Planner trade drafts until the budget is gone, and the second rejection usually means the question is unanswerable on this data |
| FR-3.6 | A revision that changes the dataset spec re-resolves the data | Otherwise the recorded plan describes a window the results did not come from |
| FR-3.7 | The Validator is fed deterministic diagnostics as numbers, not asked to infer them | The diagnostics engine runs first |
| FR-3.8 | The Validator should run on a different vendor from the Planner; when it does not, the run warns | `independence_warning` |
| FR-3.9 | A run that fails mid-pipeline returns readable, saying how far it got and why it stopped | Never half-written |
| FR-3.10 | The whole run, its steps and its outcome persist | `runs`, `run_steps`, and the serialised `RunOutcome` in `runs.outcome` |

### 5.4 Context channels

**Three channels feed the Planner: web search, document retrieval and MCP
tools. None of them may become a number.**

| ID | Requirement | Acceptance |
|---|---|---|
| FR-4.1 | Web search, when the resolved capability is on, searches before planning and appends attributed results to the Planner's context | Off by default |
| FR-4.2 | A `query_writer` role, when assigned, turns the question into up to three symbol-shaped queries first | `MAX_SEARCH_QUERIES = 3`, searched sequentially |
| FR-4.3 | With search on and no query writer assigned, the verbatim question is searched | The floor the feature never does worse than |
| FR-4.4 | A misconfigured query writer degrades to that floor rather than failing the run | Same for a misconfigured search provider |
| FR-4.5 | A failed search degrades the run rather than failing it | Search is context; losing an analysis to a search outage is the worse trade |
| FR-4.6 | A project's uploaded documents are retrieved into the Planner's context whenever the project has any | Documents-presence is the gate. No toggle |
| FR-4.7 | Retrieval is scoped by `document_chunks.project_id`, and filters on the embedding model | A join can be forgotten; a `WHERE` on the row cannot. 384 dimensions mean nothing against 1024 |
| FR-4.8 | MCP tools, when configured and allowlisted, are called by a `researcher` role in a bounded loop before planning | `MAX_RESEARCH_ROUNDS = 4`, then one tool-free call for a clean summary |
| FR-4.9 | The MCP allowlist is default-deny, exact-match, server-qualified, and has no wildcards | `files:*` is a literal tool name. A pattern would re-admit whatever a server added next |
| FR-4.10 | The gate runs **before** the session is asked, and is read per call | A refused tool is never named to the server; an allowlist change needs no restart |
| FR-4.11 | Discovery lists everything a server offers, each marked `allowed` | Listing is never permitting. It is how a user builds the list |
| FR-4.12 | **The Narrator never sees any of these three channels** | Its output is what the grounding gate judges, and web snippets are dense with numbers. A reader left with no interpretation is worse off than one left with an uninformed interpretation, so this needs a design before it changes |

### 5.5 The code escape hatch

| ID | Requirement | Acceptance |
|---|---|---|
| FR-5.1 | When no registry tool fits, a Quant Coder may write code, gated on three conditions | Project enables it; the tier has a Validator; a Quant Coder is configured. All three refuse rather than degrade |
| FR-5.2 | The Planner is only told `code_steps` exists when the capability is on | Otherwise it reaches for it on hard questions and every such plan is refused after the call is paid for |
| FR-5.3 | Code runs in a separate process with no network, a near-empty filesystem, an import allowlist, and OS-enforced memory, CPU and wall-clock caps | Every restriction has a test that tries to get out of it |
| FR-5.4 | The result is marked `unvalidated` in the tool name, the manifest, the run banner and the printout | Derived from the result itself. A marker that travels separately can be lost |
| FR-5.5 | The permission check happens before the data is fetched | A run that cannot execute what it planned should say so without spending a fetch |

### 5.6 Output

| ID | Requirement | Acceptance |
|---|---|---|
| FR-6.1 | Charts are proposed deterministically from the shape of each result | 14 spec types. Rules key on shape before name, so a tool that starts emitting `residuals` gets a QQ plot without an edit |
| FR-6.2 | Every proposed chart binds to data that exists in the result it was proposed for | `unresolved_references` comes back empty. A chart of data that does not exist is an ungrounded number with a line through it |
| FR-6.3 | No chart type can express a second y-axis | The union has no field for it, and a test asserts the absence across every member |
| FR-6.4 | Every chart has a table view of the same numbers | |
| FR-6.5 | Canvas tabs: one per chart, plus Narrative, Diagnostics, Trace and Cost | Panels are force-mounted so printing gets all of them, and inactive panels are parked off-screen rather than hidden, because a Plotly chart in `display: none` renders blank |
| FR-6.6 | Risk flags, refusals and unjudged checks stay on screen beside the charts | Not behind a tab |
| FR-6.7 | A run exports as JSON, Markdown, CSV, XLSX or a ZIP of all of them, each carrying the manifest | Built from the stored outcome. Exporting a run from last week costs one SELECT and asks no model anything |
| FR-6.8 | Charts export to PNG and SVG from the live browser graph | So the image is the one on screen |
| FR-6.9 | PDF comes from the browser's print pipeline via a stylesheet | It forces light surfaces whatever theme you read in, keeps a chart card whole across a page break, and always prints the provenance block |
| FR-6.10 | `Diagnostic.passed` is tri-state; `None` means "not judged", never "failed" | Enforced through every layer including the UI |

### 5.7 Telemetry

| ID | Requirement | Acceptance |
|---|---|---|
| FR-7.1 | Every model call is recorded as a run step with agent, provider, model, tokens, cost, latency and a parent link | Rejected attempts are steps too, because they were billed |
| FR-7.2 | A step records its prompt and its response | One prompt per attempt, since a retry is a different conversation. Both truncated at `PROMPT_LIMIT` |
| FR-7.3 | OpenTelemetry spans cover what run steps cannot see: HTTP handlers, database timings, transport | |
| FR-7.4 | **No number is summed from both sources.** `spans` has no token or cost column at all | Structural, not a convention. A test asserts the columns do not exist |
| FR-7.5 | `span()` is inert until configured and swallows sink failures | Telemetry may never break what it measures |
| FR-7.6 | OTLP export is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, with a 2s export timeout | An unreachable collector cannot hold a shutdown open |
| FR-7.7 | Rates with no denominator return `null`, not `0.0` | Zero reads as "nothing ever failed", which is a claim. "Nothing has run yet" is a different statement |

## 6. Non-functional requirements

| ID | Requirement | Notes |
|---|---|---|
| NFR-1 | Runs on a single machine, offline except for the model calls and data fetches you configure | `ECONOMETRICA_PRICE_SOURCE=synthetic` plus local Ollama needs no network at all |
| NFR-2 | API keys encrypted at rest, never plaintext in the database | `services/keystore.py` |
| NFR-3 | Python pinned to 3.12 | `arch`, `numba` and `linearmodels` publish no 3.14 wheels |
| NFR-4 | `mypy --strict` and `ruff` clean on `src` | Part of the gate |
| NFR-5 | The trimmed Plotly bundle is roughly 1 MB gzipped | Acceptable for a locally served application. It is `lib/core` plus four traces, not the whole ~3 MB |
| NFR-6 | Light and dark themes throughout, including chart palettes | Series colours validated for colour-blindness separation against the exact card surfaces they render on |
| NFR-7 | A CPU-bound fit must not make the application unusable | Open. A fit runs on the event loop, so other requests wait until it returns. Accepted for single-user local work. The medium-term job queue item revisits it |

## 7. Success metrics

**What we would measure if this were serving a team.** The instrumentation for
all seven already exists in `run_steps` and `spans`.

| Metric | Why it matters | Where it comes from |
|---|---|---|
| **Grounding gate block rate** | The single best proxy for how much a model is confabulating. Should be low and non-zero. Zero means the gate is not biting | `run_steps` narrator status |
| **Validator rejection rate** | High means the Planner and the data disagree systematically | `run_steps` validator verdicts |
| **Refusal rate by tool** | A tool refused often is a tool being reached for wrongly, which is a prompt or catalogue problem | Step outcomes |
| **Plan revision count** | Rising means questions are getting harder than the registry | `runs.revisions` |
| **Re-run reproduction rate** | Falling means a data source is revising history | Re-run reports |
| **Cost per completed run, by provider and role** | The economic question. Local Ollama for classification, frontier for planning | `run_steps` tokens and cost |
| **Latency p50/p95/p99 by handler** | | `spans` |

## 8. Release criteria

**All seven are met, as of the close of phase 6.**

- [x] All six phases pass their gate tests
- [x] `ruff` and `mypy --strict` clean on `src`
- [x] `alembic check` reports no drift
- [x] Six Playwright tests pass from a cold start. Four run against a live
      local model, and one of those four runs on real market data
- [x] A run can be reproduced from its manifest through the UI, verified
      against a live model
- [x] A run can be exported in all five formats and the manifest is present in
      each
- [x] Every restriction on the code sandbox has a test that attempts to defeat
      it. Neutering the audit hook fails 12 of 28 escape tests, which is how
      they were shown to bite

## 9. Risks and open items

**Six risks. One is high, and it is disclosed: a stdio MCP server runs with
host privileges.**

| Risk | Severity | Position |
|---|---|---|
| A CPU-bound fit blocks the event loop, so other requests wait | Medium | Acceptable at single-user scale. The designed process pool would have taken the fit off the loop, and it was not built. The [medium term](../5-roadmap/medium-term.md) job queue item revisits it |
| The Narrator has no access to context channels, so interpretations are less informed than they could be | Medium | Deliberate and unresolved. Fixing it needs a design that answers the grounding-gate problem first, not a toggle |
| A stdio MCP server is an arbitrary local command with host privileges | High, and disclosed | It is not sandboxed like the quant coder. The allowlist gates which tools run, not what the process can do. HTTP is the choice for a server you do not fully trust |
| Vendors revise adjusted-close history, so old manifests stop reproducing | Low, by design | This is a finding, and the re-run report names it |
| The keyless DuckDuckGo provider scrapes HTML with no API contract | Low | It degrades to no search rather than failing a run, and it has a live test for exactly this reason |
| The single-user assumption is baked into the absence of an auth layer | Medium | Deliberate for v1. See [N1](#32-non-goals) |

**Next, 2 minutes:** open the [capability inventory](capabilities.md) and
search it for one tool you use, such as `garch` or `capm`.

---

[Documentation](../README.md) · [1. Why](../1-why/) ·
[2. Product](README.md) · **PRD** · [Capabilities](capabilities.md) ·
[Journeys](journeys.md) · [3. Architecture](../3-architecture/) ·
[4. Decisions](../4-decisions/) · [5. Roadmap](../5-roadmap/) ·
[6. The art of the possible](../6-art-of-the-possible/)
