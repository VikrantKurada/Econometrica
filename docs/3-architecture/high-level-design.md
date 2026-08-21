# High-level design

The system in the large. What the components are, how a request moves through
them, and why the boundaries fall where they do.

---

## 1. Context

Econometrica is a single-process backend and a single-page frontend, on one
machine, talking to a local Postgres and to whatever model providers and data
sources you have configured.

```mermaid
flowchart LR
    U(("User"))

    subgraph LOCAL["One machine"]
        FE["Vite dev server<br/>or static build"]
        BE["FastAPI<br/>uvicorn"]
        PG[("Postgres 16<br/>TimescaleDB<br/>pgvector")]
        FS[("storage/<br/>blobs · price cache<br/>encrypted keys")]
        OL["Ollama"]
        SBX["Sandbox<br/>child process"]
    end

    subgraph OUT["Outside, all optional"]
        ANT["Anthropic"]
        OAI["OpenAI"]
        GEM["Gemini"]
        NIM["NVIDIA NIM"]
        YF["Yahoo Finance"]
        FRED["FRED"]
        KF["Ken French library"]
        SRCH["DuckDuckGo / Brave"]
        MCP["MCP servers"]
    end

    U --> FE --> BE
    BE --> PG
    BE --> FS
    BE --> OL
    BE -.-> SBX
    BE -.-> ANT & OAI & GEM & NIM
    BE -.-> YF & FRED & KF
    BE -.-> SRCH
    BE -.-> MCP
```

Everything dotted is optional. With `ECONOMETRICA_PRICE_SOURCE=synthetic` and
a local Ollama, nothing leaves the machine at all, and the whole pipeline
still runs end to end.

**Single user, no authentication.** This is a deliberate scope decision, not
an oversight. It keeps the local Ollama instance reachable without a proxy and
removes an entire tier of the product. It is also the first thing the
[medium-term roadmap](../5-roadmap/medium-term.md) revisits.

## 2. Components

```mermaid
flowchart TB
    subgraph FE["Frontend"]
        SHELL["AppShell<br/>three panes, resizable, collapsible"]
        TREE["ProjectTree"]
        CANVAS["ArtifactCanvas<br/>tabs, banner, findings, export"]
        CHATP["Conversation<br/>token stream"]
        CHARTS["14 chart renderers<br/>+ palette + theme"]
        DATA["ProjectData<br/>upload, map, confirm"]
        TELE["TraceGraph · CostDashboard"]
        SHELL --> TREE & CANVAS & CHATP
        CANVAS --> CHARTS & TELE
        CANVAS --> DATA
    end

    subgraph BE["Backend"]
        ROUTERS["api/routers<br/>11 routers, 32 endpoints"]
        SERVICES["services/<br/>capabilities · ingest · rag · exports · keystore"]
        ORCH["agents/orchestrator"]
        ROLES["10 agent roles"]
        REG["econ/registry<br/>37 tools · gates · diagnostics"]
        CHARTSPEC["charts/propose<br/>deterministic"]
        LLM["llm/<br/>5 providers behind one interface"]
        DATASRC["data/<br/>yahoo · fred · famafrench · uploaded · synthetic · cache"]
        CTX["tools/ web_search · retrieval<br/>mcp/ connect · allowlist"]
        SAND["sandbox/<br/>policy · limits · runner · child"]
        TELEM["telemetry/<br/>spans · metrics · writer"]

        ROUTERS --> SERVICES
        ROUTERS --> ORCH
        ORCH --> ROLES
        ROLES --> REG
        ROLES --> LLM
        ROLES --> DATASRC
        ROLES --> CTX
        ROLES --> SAND
        ORCH --> CHARTSPEC
        ROUTERS --> TELEM
    end

    FE <-->|"REST + SSE"| ROUTERS
```

### What each backend package is responsible for

| Package | Owns | Must not |
|---|---|---|
| `api/routers/` | HTTP shape, composition of a run from a project's settings | Contain analysis logic |
| `services/` | Anything needing a database session that is not a route | Be imported by `agents/` |
| `agents/` | Role behaviour, the pipeline, the typed contracts | Know about projects, chats, or `db.models` |
| `econ/` | All computation, the registry, gates, diagnostics | Leak a library result object |
| `charts/` | Which chart a result shape implies | Ask a model anything |
| `llm/` | Five adapters, one interface, capability flags | Leak a vendor SDK type |
| `data/` | Price, rate and factor sources; the on-disk cache | Import from `agents/` |
| `tools/` | Context channels: web search, the retrieval protocol | Ever become a source of numbers |
| `mcp/` | Server config, transports, the allowlist | Let an unlisted tool reach a server |
| `sandbox/` | The policy, the OS caps, the child, the runner | Be mistaken for a correctness check |
| `telemetry/` | Spans, the writer, the metrics query | Carry tokens or cost |

## 3. The agent pipeline

Ten roles. Two of them are deterministic and have no model at all, which is
the part people find surprising. (The tenth, the Column Mapper, sits on the
upload path rather than in a run, so it is absent from the diagram below.)

```mermaid
flowchart TB
    START(["A question"]) --> RESEARCH
    RESEARCH["<b>Researcher</b><br/>bounded MCP tool loop<br/><i>only if MCP configured + allowlisted</i>"] --> RETRIEVE
    RETRIEVE["<b>Retrieval</b><br/>project documents into context<br/><i>only if the project has documents</i>"] --> SEARCH
    SEARCH["<b>QueryWriter → Web search</b><br/>symbol-shaped lookups<br/><i>only if the capability is on</i>"] --> PLAN

    PLAN["<b>Planner</b><br/>a typed AnalysisPlan"] --> PERM{"code steps<br/>permitted?"}
    PERM -->|no, and there are some| REFUSE(["Refused, before any fetch"])
    PERM -->|yes, or none| STEWARD

    STEWARD["<b>Data Steward</b> · deterministic<br/>resolve, align, convert, return-ise<br/>+ a quality report"] --> ECON
    ECON["<b>Econometrician</b> · deterministic<br/>bind, gate, execute"] --> CODE
    CODE["<b>Quant Coder</b><br/><i>only if all three gates open</i>"] --> DIAG
    DIAG["<b>Diagnostics engine</b> · deterministic"] --> CHARTS2
    CHARTS2["<b>propose_charts</b> · deterministic"] --> VAL

    VAL{"<b>Validator</b><br/>tier-dependent"}
    VAL -->|approved| NARR
    VAL -->|rejected, revisions left| REVISE["<b>Planner</b> again,<br/>with the reasons"]
    VAL -->|rejected, budget spent| NARR
    REVISE --> RESOLVE{"dataset<br/>changed?"}
    RESOLVE -->|yes| STEWARD
    RESOLVE -->|no| ECON

    NARR["<b>Narrator</b>"] --> GATE{"<b>Grounding gate</b>"}
    GATE -->|"every number matches"| DONE(["Published"])
    GATE -->|"one does not"| WITHHELD(["Withheld, with the reason"])

    style REFUSE fill:#f8d7da,stroke:#e34948,color:#14181d
    style WITHHELD fill:#fff3cd,stroke:#eda100,color:#14181d
    style DONE fill:#d4edda,stroke:#1baf7a,color:#14181d
```

### Three decisions the orchestrator makes that no single role can

**Which tier.** `single` is cheap and skips the Validator. `critic` is the
default and consults it. `consensus` plans on several providers and reports
what they disagree about. The deterministic gates run in **every** tier.
Cheap must not mean unguarded.

**When to stop.** A rejection buys exactly one revision. Left unbounded, a
Validator and a Planner will trade drafts until the budget runs out, and the
second rejection is far more likely to mean "this question cannot be answered
with this data" than "try once more".

**What a failure leaves behind.** A run that dies mid-pipeline comes back
readable, saying how far it got and why it stopped. Never half-written.

### Why the Data Steward has no model

The design lists it as one of the six agent roles. Nothing it does needs a
model. Aligning calendars, converting frequency and constructing returns each
have exactly one right answer, and **a reproducibility manifest means nothing
if the data under it depended on what a model felt like that morning**.

The genuinely model-shaped part of data handling, mapping the columns of an
uploaded file to roles, is a separate role, and a person confirms its output
before anything is ingested.

## 4. Data flow through a run

```mermaid
flowchart LR
    subgraph IN["Resolution"]
        SPEC["DatasetSpec<br/>tickers, window,<br/>frequency, returns,<br/>risk_free, factors"]
        PS["build_project_source<br/><b>upload-first</b>"]
        UP[("Project uploads<br/>hypertable")]
        MKT["Market source<br/>+ disk cache"]
        RATE["FRED rates"]
        FF["Ken French factors"]
        FRAME["An aligned frame<br/>+ DataQualityReport"]

        SPEC --> PS
        PS -->|"symbol in a dataset"| UP
        PS -->|"everything else"| MKT
        SPEC --> RATE
        SPEC --> FF
        UP & MKT & RATE & FF --> FRAME
    end

    subgraph RUN["Execution"]
        FRAME --> GATE{"Gate check<br/>per step"}
        GATE -->|passes| TOOL["Registry tool"]
        GATE -->|fails| REF["Refusal<br/>with a reason"]
        TOOL --> RS["ResultSet<br/>+ Manifest"]
    end

    subgraph OUT["Artifacts"]
        RS --> CH["ChartSpecs"]
        RS --> DG["Diagnostics"]
        RS --> NA["Narration<br/><i>gated</i>"]
        RS --> TR["Trace records"]
        CH & DG & NA & TR --> OUTCOME["RunOutcome<br/>JSONB in runs.outcome"]
    end
```

### Provenance travels on two channels, and that is not redundancy

`DataQualityReport.source` is read from the source's `label` **before anything
is fetched**, so it cannot describe a mixed run. The label says what was
*available*.

A post-fetch `mixed_sources` **info** flag says what was *used*, naming every
ticker under the source that served it. That is the authoritative record.

Info rather than warning, because mixing is the feature. An uploaded index
against a listed stock is not answerable any other way, and that question is
exactly what exposed the gap.

## 5. Storage

One engine doing three jobs.

```mermaid
erDiagram
    PROJECTS ||--o{ CHATS : owns
    PROJECTS ||--o{ DATASETS : owns
    PROJECTS ||--o{ DOCUMENTS : owns
    PROJECTS ||--o{ DOCUMENT_CHUNKS : "scoped by column"
    CHATS ||--o{ MESSAGES : has
    CHATS ||--o{ RUNS : has
    RUNS ||--o{ RUN_STEPS : has
    RUN_STEPS ||--o{ RUN_STEPS : "parent_id"
    DATASETS ||--o{ OBSERVATIONS : "hypertable"
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : has

    PROJECTS {
        uuid id PK
        string name
        bool web_search_enabled
        bool mcp_enabled
        bool code_sandbox_enabled
        jsonb mcp_servers
        jsonb mcp_allowlist
        string validation_tier
        jsonb model_assignments
    }
    RUNS {
        uuid id PK
        text question
        string status
        string tier
        int revisions
        jsonb outcome
        int input_tokens
        float cost_usd
    }
    RUN_STEPS {
        bigint seq
        uuid parent_id FK
        string agent
        string kind
        string status
        int attempt
        text prompt
        text response
    }
    OBSERVATIONS {
        timestamptz ts PK
        string symbol PK
        string field PK
        float value
    }
    DOCUMENT_CHUNKS {
        uuid document_id PK
        int ordinal PK
        uuid project_id
        vector embedding
        string embedding_model
    }
    SPANS {
        string trace_id PK
        string span_id PK
        string name
        float duration_ms
        jsonb attributes
    }
```

| Job | Mechanism |
|---|---|
| Time series at volume | TimescaleDB hypertable on `observations` |
| Semi-structured artifacts | JSONB: `runs.outcome`, `model_assignments`, `mcp_servers`, span attributes |
| Semantic retrieval | pgvector on `document_chunks.embedding` |

Three facts about the hypertable that Alembic cannot see, all of which cost
time to discover:

1. **The conversion is invisible to autogenerate.** `create_hypertable` is
   hand-written in the migration and asserted against Timescale's catalogue in
   a test.
2. **It creates its own index**, which made `alembic check` want to drop one
   on every run. So `create_default_indexes => FALSE` and we declare
   `ix_observations_ts` ourselves.
3. **`field` is part of the primary key.** A wide file mapping both a close
   and a volume has two rows per `(ts, symbol)`.

### Ordering

Transcripts order on `Message.seq`, a Postgres identity column, **never** on
`created_at`. That is the transaction timestamp, so rows written together tie
exactly.

The same applies to datasets. Ordering on `created_at` is `func.now()`, which
is transaction start, so the "newest upload wins" rule is well ordered in use
(two confirmations are two requests) but a test writing two datasets at once
must set the column itself or assert a coin flip.

## 6. Transport

Two shapes, chosen per endpoint by whether the answer arrives at once.

| Shape | Used by | Why |
|---|---|---|
| REST + JSON | Everything with a definite answer | |
| Server-sent events | `POST /api/chats/{id}/messages`, `POST /api/chats/{id}/runs` | The answer arrives over time and the user should be able to watch it |

**Runs are a separate route from messages, not a mode flag.** The two differ
in every way that matters to a client: a different event vocabulary (phases,
not tokens), a different failure model (a refused run still returns results),
and a turn that can take minutes. Folding them together would make one route's
response shape depend on a request field, which neither the frontend's stream
reader nor its tests could narrow on.

Run events use dotted names (`plan.finished`, `step.finished`) rather than a
discriminated union, because a client renders a timeline and a new phase must
not break one that has not been updated.

## 7. Deployment

```mermaid
flowchart TB
    subgraph DEV["Development, via start.ps1"]
        D1["docker compose up -d db<br/>Postgres on host port 5433"]
        D2["alembic upgrade head"]
        D3["uvicorn on <b>8001</b>, no --reload"]
        D4["vite dev on 5173,<br/>proxying to ECONOMETRICA_API_URL"]
        D1 --> D2 --> D3 --> D4
    end

    subgraph E2E["End to end"]
        E1["uvicorn on <b>8100</b><br/>ECONOMETRICA_PRICE_SOURCE=synthetic"]
        E2["uvicorn on <b>8101</b><br/>ECONOMETRICA_PRICE_SOURCE=yahoo"]
        E3[("one shared Postgres")]
        E1 --> E3
        E2 --> E3
    end
```

**Port 8000 is avoided on purpose, and naming `127.0.0.1` does not save you.**
Another container on this machine holds the wildcard address on 8000, and a
wildcard socket answers `127.0.0.1` traffic too. Measured: with uvicorn bound
to `127.0.0.1:8000` and the OS naming it as the listener, 25 consecutive
health polls came back 404 with `Server: SurrealDB`, and the same URL answered
200 from uvicorn minutes later. It is a race, not a rule, which is exactly why
it reads as "the fix did not work".

**Two e2e backends, not one flag.** `analysis.spec.ts` and `canvas.spec.ts`
assert that generated prices *say so*, and that only means something while the
generator is what they get. On 8101 the `synthetic_data` flag is asserted
**absent**. That is the other half of the same seam: a flag that cried wolf
would be worse than no flag.

**`uvicorn --reload` cannot be stopped by port.** The reloader binds the
socket in the parent and hands it to the child, so killing either leaves an
orphan holding it. The next start fails to bind and every request reaches the
old code, which looks exactly like a fix not working. `start.ps1` runs without
`--reload` and records the window pids so `-Stop` can kill the whole tree.

## 8. Quality gates

| Gate | Command |
|---|---|
| Backend tests | `uv run pytest -q` |
| Lint | `uv run ruff check src tests alembic` |
| Types | `uv run mypy src` |
| Migrations | `uv run alembic upgrade head` then `alembic check` |
| Frontend tests | `npx vitest run` |
| Frontend types | `npx tsc --noEmit` |
| End to end | `npm run test:e2e` |

Two things about the test suite that are load-bearing:

**A test asserting the registry is populated must run in a subprocess.** Every
module under `tests/econ` imports the family it exercises, so by collection
time the in-process registry is full no matter what the application does.
That is precisely how it stayed empty in a live server until phase 4.

**The test database is built by `Base.metadata.create_all`, not from the
migrations.** So a constraint test passing against Postgres says nothing about
whether a revision exists for it. `tests/db/test_migrations.py` asserts every
constraint, and every *value* of each vocabulary, reaches some migration.
Asserting the constraint names alone was not enough: `ck_run_steps_agent_known`
had been in the initial revision since phase 4, so adding `quant_coder` to the
Python tuple left the test green while a fresh database rejected every sandbox
step.

---

[Documentation](../README.md) · [3. Architecture](README.md) ·
**High-level design** · [Low-level design](low-level-design.md) ·
[Blueprints](blueprints.md) · [Integration patterns](integration-patterns.md)
