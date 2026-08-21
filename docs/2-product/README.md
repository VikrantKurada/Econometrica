<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/doc-product-workbench-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../assets/doc-product-workbench-light.svg">
  <img alt="The Econometrica workbench: a three-pane window, and what a run leaves behind" src="../assets/doc-product-workbench-light.svg">
</picture>

# 2. What is in the box

The product manager's view. What it does, who it is for, what it deliberately
refuses, and how you would know whether it is working.

| Page | What it covers |
|---|---|
| **This page** | The product in one sitting: the shape, the loop, the guarantees |
| **[Product Requirements Document](prd.md)** | The full PRD: goals, users, requirements, acceptance criteria, non-goals, metrics |
| **[Capability inventory](capabilities.md)** | Everything that exists, enumerated: 37 tools, 14 charts, 5 providers, 10 agent roles, every endpoint |
| **[User journeys](journeys.md)** | Four people, four sessions, what each of them actually does |

## The one-sentence version

**Econometrica is a local workbench where you ask an econometric question in
prose and get back charts, an interpretation and a reproducibility manifest,
with the arithmetic done by tested functions rather than by a language model.**

## The shape of it

One window. Three panes. No login, no cloud, no account.

```
┌────────────┬─────────────────────────────────┬──────────────┐
│ Projects   │   Artifact Canvas               │  Chat        │
│  └ Chats   │   charts, tables, findings,     │  streaming   │
│            │   trace, cost, exports          │  conversation│
│            │                                 │              │
│  260px     │   flexible                      │  420px       │
└────────────┴─────────────────────────────────┴──────────────┘
```

All three resize and collapse. Collapsing the last one standing is refused,
because that leaves you looking at an empty window with no obvious way back.

**The chat pane and the canvas are different things, and users conflate them.**
A chat message streams tokens from one model and calls no tools, so it can
never produce a chart. Runs start from the canvas composer. This tripped up
the very first user of the application, who read the chat's empty state,
asked for an analysis there, and got prose and no charts. The empty state is
different now. If you take one thing from this page as a designer: two
adjacent text boxes that do different things need to say so before the user
finds out.

## The loop

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant C as Canvas
    participant O as Orchestrator
    participant T as Tool registry
    participant DB as Postgres

    U->>C: A question, and which model should plan it
    C->>O: POST /api/chats/{id}/runs
    O-->>C: run.started
    O->>O: research, retrieve, search for context
    O-->>C: plan.finished (the typed AnalysisPlan)
    O->>O: resolve the data, report its quality
    O-->>C: data.finished (rows, flags, source)
    loop each planned step
        O->>T: run this tool with these params
        T-->>O: a ResultSet, or a refusal with a reason
        O-->>C: step.finished
    end
    O-->>C: charts.finished
    O->>O: diagnostics, then the Validator
    O-->>C: validate.finished (approved or rejected)
    O->>O: narrate, then check every number
    O-->>C: narrate.finished (published, or withheld and why)
    O->>DB: persist the run, its steps and its outcome
    O-->>C: run.finished
    C-->>U: charts, narrative, diagnostics, trace, cost
```

Every arrow back to the canvas is a server-sent event, so the run is watchable
rather than a spinner. A run that dies halfway comes back describing itself:
how far it got, what it produced before it stopped, and why it stopped. It is
never half-written.

## The four guarantees

These are the product promises. Each one is a mechanism, and each mechanism
has a test.

### 1. No number comes from a model

Models choose tools. Tools compute. There are 37 of them across five families,
each typed, versioned and unit-tested against known-answer fixtures and
property-based invariants (the beta of an asset regressed on itself is 1;
VR(1) is 1; simulated GARCH parameters are recovered inside their confidence
interval; Johansen rank is recovered on synthetic cointegrated systems).

### 2. A tool refuses work the data cannot support

Preconditions are executable, not advisory. A `Gate` is checked against the
real series before the tool runs.

```mermaid
flowchart LR
    S["The resolved series"] --> G{"Gate:<br/>arch_effects?"}
    G -->|present| RUN["garch runs"]
    G -->|absent| REF["Refused, with the reason.<br/>The step appears in the plan,<br/>the trace and the canvas<br/>as a refusal."]
    style REF fill:#fff3cd,stroke:#eda100,color:#14181d
```

A refusal is not an error. It is a result: it says the question you asked
cannot be answered this way on this data, which is often the most useful thing
anyone will tell you that day. Refusals surface in the canvas alongside the
charts rather than behind a tab.

### 3. Prose is checked against the results

Every number the Narrator writes is extracted and matched against
`ResultSet.all_numeric_values()`. One that does not match withholds the whole
narration.

Precision comes from the citation, not from a global tolerance. "1.30" claims
two decimal places and matches any computed value that rounds to 1.30 at two
places. "1.3" claims one. That is both stricter and more permissive than a
fixed epsilon, in the right directions.

Narrow, deliberate exemptions exist, each with a test that it does not apply
outside its context: reference words ("figure 2", "step 3"), citations of step
ids the plan actually contains (`(s3)` passes, `(s7)` does not), and
`YYYY-YYYY` year ranges. That last one was found by running a real narration:
a model titled its answer "(2020-2024)" and the gate read both years as
fabrications.

The tolerance itself has never moved. There is a test asserting that a
`-15.066` case still fails, and it sits directly beside the exemptions so that
nobody loosens one without seeing it.

### 4. Everything reproduces

Every result carries a manifest: a SHA-256 fingerprint of the exact aligned
input matrix, the tool name and version, a parameters hash, library versions
and any RNG seed.

`POST /api/runs/{id}/rerun` re-executes the recorded plan against freshly
resolved data and reports, per step, whether the numbers came back the same.
It **consults no model**. Re-planning would test whether a model repeats
itself, which is a different question and not one a manifest promises anything
about.

When a re-run disagrees, that is a finding about the data, not a bug. The
report names the step and the reason: the fingerprint changed, so the source
is not serving the same history; the tool moved version; the parameters hash
differently; or the numbers simply differ.

## What it will not do

A product is defined as much by its refusals. These are deliberate.

| It will not | Because |
|---|---|
| Invent data when no source is configured | A run refuses with an explanation. Generating something plausible is the failure this project exists to prevent. |
| Let a chat produce charts | A chat is one model streaming tokens. A run is a pipeline. Conflating them is how the first user got prose instead of analysis. |
| Publish an interpretation containing an unmatched number | The whole narration is withheld. A quietly repaired paragraph is worse than none. |
| Let a model's column mapping be ingested without a person confirming it | `confirm_mapping` is the only thing that produces a mapping the ingest will act on. The model may only reorder candidates the deterministic profiler already found admissible. |
| Draw a second y-axis | The union has no field for it anywhere, and a test asserts the absence across every chart type. Two measures on two scales share a crossing point that is an artifact of the scaling, and readers reliably read causation into it. Two measures become a stacked panel chart. |
| Let a sandbox result look like a registry result | It is marked in the tool name, the manifest, the run banner and the printout. |
| Let anything read from the web, a document or an MCP tool become a number | The grounding gate admits only what a registry tool computed. There is a test proving a figure quoted verbatim out of a search snippet is still blocked. |
| Serve a stale cache entry when the source is unreachable | It raises instead. There is no channel to disclose staleness, because the source label is read before the fetch. |

## Status

All six build phases are complete.

| Phase | What it delivered | State |
|---|---|---|
| 0 | Scaffold | done |
| 1 | Database, API, three-pane shell | done |
| 2 | Econometrics core: 37 tools, 5 families | done, 97% coverage |
| 3 | Five LLM providers, streaming chat | done |
| 4 | Multi-agent orchestration, diagnostics, grounding gate | done |
| 5 | Charts and the artifact canvas | done |
| 6 | Real data, uploads, telemetry, MCP, retrieval, sandbox | done |

**1,492 backend tests, 326 frontend tests, 6 Playwright end to end.** `ruff`
and `mypy --strict` clean on `src`. `alembic check` reports no drift.

The end to end suite drives the whole application from a cold start against a
live local model and real market data: a project, an upload profiled and
confirmed into a Timescale hypertable, an analysis on a real ticker, the
charts and trace DAG and cost dashboard read back in the browser, a ZIP
export, and a re-run that reproduces the numbers from the manifest.

## Read next

- **[The full PRD](prd.md)** if you need requirements with acceptance criteria
- **[The capability inventory](capabilities.md)** if you want the complete list
  of what exists
- **[User journeys](journeys.md)** if you want to see it used
- **[The architect's view](../3-architecture/)** if you want to know how it is
  built

---

[Documentation](../README.md) · [1. Why](../1-why/) · **2. Product** ·
[3. Architecture](../3-architecture/) · [4. Decisions](../4-decisions/) ·
[5. Roadmap](../5-roadmap/) · [6. The art of the possible](../6-art-of-the-possible/)
