# Technical blueprints

- **The point:** twelve reference drawings, B1 to B12. Nothing here argues for
  anything.
- **Read time:** about 7 minutes end to end. Do not read it end to end. Keep
  it open on a second monitor.
- **Do first:** pick the drawing for what you are working on from the list
  below.

| You are working on | Open |
|---|---|
| Imports and layering | B1 |
| The database | B2 |
| A run, its states, its events | B3, B4, B5, B11 |
| Uploads, re-run, capabilities | B6, B7, B10 |
| The sandbox, MCP, ports | B8, B9, B12 |

- [B1. Module dependency map](#b1-module-dependency-map)
- [B2. The data model](#b2-the-data-model)
- [B3. Run lifecycle](#b3-run-lifecycle)
- [B4. Run state machine](#b4-run-state-machine)
- [B5. Step state machine](#b5-step-state-machine)
- [B6. Upload lifecycle](#b6-upload-lifecycle)
- [B7. Re-run and reproduction](#b7-re-run-and-reproduction)
- [B8. The sandbox process](#b8-the-sandbox-process)
- [B9. MCP research loop](#b9-mcp-research-loop)
- [B10. Capability resolution](#b10-capability-resolution)
- [B11. Streaming event vocabulary](#b11-streaming-event-vocabulary)
- [B12. Deployment topology](#b12-deployment-topology)

---

## B1. Module dependency map

**The arrows are the allowed directions. An arrow that does not appear here is
a layering violation.**

- Four forbidden edges are drawn in red. They are the ones somebody would
  plausibly add.
- Two of the four are caught by a test.
- Two are caught by the structure itself.

```mermaid
flowchart TB
    MAIN["main.py"]
    ROUTERS["api/routers/"]
    DEPS["api/deps.py"]
    SERVICES["services/"]
    DBM["db/models/"]
    AGENTS["agents/"]
    ECON["econ/"]
    CHARTS["charts/"]
    LLM["llm/"]
    DATA["data/"]
    TOOLS["tools/"]
    MCPM["mcp/"]
    SAND["sandbox/"]
    TELE["telemetry/"]
    SCHEMAS["schemas/"]

    MAIN --> ROUTERS
    MAIN --> ECON
    MAIN --> TELE
    ROUTERS --> DEPS
    ROUTERS --> SERVICES
    ROUTERS --> AGENTS
    ROUTERS --> SCHEMAS
    ROUTERS --> DBM
    ROUTERS --> LLM
    ROUTERS --> DATA
    ROUTERS --> MCPM
    ROUTERS --> TOOLS
    SERVICES --> DBM
    SERVICES --> LLM
    SERVICES --> TOOLS
    AGENTS --> ECON
    AGENTS --> LLM
    AGENTS --> DATA
    AGENTS --> TOOLS
    AGENTS --> MCPM
    AGENTS --> SAND
    AGENTS --> CHARTS
    CHARTS --> ECON
    DATA --> ECON
    SAND --> ECON
    TELE --> DBM

    AGENTS -.->|"FORBIDDEN"| DBM
    AGENTS -.->|"FORBIDDEN"| SERVICES
    DATA -.->|"FORBIDDEN"| AGENTS
    TOOLS -.->|"FORBIDDEN"| ECON

    linkStyle 26,27,28,29 stroke:#e34948,stroke-width:2px,stroke-dasharray:5 5
```

| Forbidden edge | Enforced by | Why |
|---|---|---|
| `agents/` to `db.models` | Review plus the protocol split (`tools/retrieval.py`) | An agent that knows about projects cannot be tested without a database |
| `agents/` to `services/` | Same | Capabilities are resolved by the router and passed in |
| `data/` to `agents/` | `tests/data/test_layering.py`, including a **subprocess** check of both import orders | It is the lower layer, and importing upward is a cycle the moment the steward calls down |
| `tools/` producing numbers | The grounding gate, with a test per channel | `tools/` is context, `econ/` is computation |

**The subprocess check is not paranoia.** In-process, both modules are already
in `sys.modules` by collection time, so an in-process test proves nothing.

---

## B2. The data model

**Ten tables. Everything hangs off `PROJECTS` except `SPANS`.**

```mermaid
erDiagram
    PROJECTS ||--o{ CHATS : "cascade"
    PROJECTS ||--o{ DATASETS : "cascade"
    PROJECTS ||--o{ DOCUMENTS : "cascade"
    PROJECTS ||--o{ DOCUMENT_CHUNKS : "denormalised project_id"
    CHATS ||--o{ MESSAGES : "cascade"
    CHATS ||--o{ RUNS : "cascade"
    RUNS ||--o{ RUN_STEPS : "cascade"
    RUN_STEPS ||--o{ RUN_STEPS : "parent_id, ON DELETE SET NULL"
    DATASETS ||--o{ OBSERVATIONS : "cascade, hypertable"
    DOCUMENTS ||--o{ DOCUMENT_CHUNKS : "cascade"

    PROJECTS {
        uuid id PK
        string_200 name "ck: not blank"
        text description
        bool web_search_enabled "default false"
        bool mcp_enabled "default false"
        bool code_sandbox_enabled "default false"
        jsonb mcp_servers
        jsonb mcp_allowlist
        string validation_tier "ck: single|critic|consensus"
        jsonb model_assignments "role -> provider + model"
        timestamptz created_at
    }

    CHATS {
        uuid id PK
        uuid project_id FK
        string_200 name "ck: not blank"
        bool_null web_search_enabled "null = inherit"
        bool_null mcp_enabled "null = inherit"
    }

    MESSAGES {
        bigint seq UK "identity, order on THIS"
        uuid chat_id FK
        string_20 role
        text content
        string provider
        string model
        int input_tokens
        int output_tokens
        int cache_read_tokens
        int cache_write_tokens
        float latency_ms
        string stop_reason
        text error
    }

    RUNS {
        uuid id PK
        uuid chat_id FK
        text question "ck: not blank"
        string_20 status "ck: running|completed|blocked|failed"
        string_20 tier "ck: single|critic|consensus"
        int revisions "ck: >= 0"
        text error
        jsonb outcome "the whole serialised RunOutcome"
        int input_tokens
        int output_tokens
        float cost_usd "ck: >= 0"
        float latency_ms
    }

    RUN_STEPS {
        bigint seq UK "identity"
        uuid id PK
        uuid run_id FK
        uuid parent_id FK "ck: not self"
        string_30 agent "ck: 8 known agents"
        string_10 kind "ck: llm|tool"
        string_10 status "ck: ok|refused|failed|skipped"
        int attempt "ck: >= 1"
        string provider
        string model
        int input_tokens
        int output_tokens
        float cost_usd "ck: >= 0"
        float latency_ms
        string tool
        string_64 tool_call_hash "indexed"
        text prompt "truncated at PROMPT_LIMIT"
        text response "truncated at PROMPT_LIMIT"
    }

    DATASETS {
        uuid id PK
        uuid project_id FK
        string_200 name "ck: not blank"
        string_500 filename
        string_1000 blob_path
        string_300 source_label "refuses 'synthetic'"
        string_64 fingerprint
        int rows "ck: >= 0"
        jsonb column_roles
        timestamptz created_at "func.now = transaction start"
    }

    OBSERVATIONS {
        uuid dataset_id FK
        timestamptz ts PK
        string_64 symbol PK
        string_16 field PK "close, volume, ..."
        float value
    }

    DOCUMENTS {
        uuid id PK
        uuid project_id FK
        string_500 name "ck: not blank"
        string_64 fingerprint
        int chars
        int chunks_count "ck: >= 0"
    }

    DOCUMENT_CHUNKS {
        uuid document_id PK
        int ordinal PK "ck: >= 0"
        uuid project_id "denormalised on purpose"
        text text
        vector_384 embedding
        string_100 embedding_model "filtered on"
    }

    SPANS {
        string_32 trace_id PK
        string_16 span_id PK
        string_16 parent_span_id
        string_200 name "indexed"
        string_20 kind
        string_10 status
        text detail
        timestamptz started_at "indexed"
        timestamptz ended_at
        float duration_ms "ck: >= 0"
        jsonb attributes
    }
```

### Invariants that are not visible in the schema

| Invariant | Where it lives |
|---|---|
| `observations` is a **hypertable** | Hand-written `create_hypertable` in the migration, asserted against Timescale's catalogue in a test. Autogenerate cannot see it |
| `create_default_indexes => FALSE` | Otherwise Timescale creates `observations_ts_idx` and `alembic check` wants to drop an index on every run. We declare `ix_observations_ts` ourselves |
| `spans` has **no token or cost columns** | A test asserts they do not exist. This is what makes double-counting structurally impossible |
| Ordering is on `seq`, never `created_at` | `created_at` is `func.now()`, the transaction timestamp, so rows written together tie exactly |
| Every NOT NULL column pairs a Python `default` with a `server_default` | So non-ORM inserts land valid rows |

### Alembic's blind spot

**Tests are the only gate on CHECK constraints. `alembic check` verifies
nothing about them.**

| Case | What autogenerate does |
|---|---|
| A CHECK on a table it **creates** | Emits it. The `runs` and `run_steps` revision carries all thirteen |
| A CHECK added to or changed on a table that already **exists** | Sees nothing. The revision comes out empty and has to be hand-written |

**Asserting the constraint *names* is not enough.**

1. `ck_run_steps_agent_known` has been in the initial revision since phase 4.
2. `quant_coder` was added to `STEP_AGENTS`.
3. The name test stayed green.
4. A fresh database rejected every sandbox step.

`tests/db/test_migrations.py` now asserts every *value* of each vocabulary
reaches a migration too.

---

## B3. Run lifecycle

**The full sequence, from the click to the persisted row.**

```mermaid
sequenceDiagram
    autonumber
    participant FE as Canvas
    participant RT as runs router
    participant CAP as resolve_capabilities
    participant OR as Orchestrator
    participant CTX as Context channels
    participant PL as Planner
    participant DS as Data Steward
    participant EC as Econometrician
    participant VA as Validator
    participant NA as Narrator
    participant TR as services/tracing
    participant DB as Postgres

    FE->>RT: POST /api/chats/{id}/runs
    RT->>DB: load chat + project
    RT->>CAP: resolve(project, chat)
    CAP-->>RT: web_search, mcp, code_sandbox, tier
    Note over RT: build ONLY the collaborators<br/>the capabilities permit
    RT->>OR: construct
    RT-->>FE: SSE stream opens

    OR-->>FE: run.started
    OR->>CTX: research (MCP), retrieve (docs), search (web)
    CTX-->>OR: context, attributed
    OR->>PL: plan(question, context)
    PL-->>OR: AnalysisPlan, validated at construction
    OR->>OR: check_permitted(code steps)
    OR-->>FE: plan.finished

    OR->>DS: resolve(plan.dataset)
    DS-->>OR: frame + DataQualityReport
    OR-->>FE: data.finished

    loop each ordered step
        OR->>EC: run step
        EC->>EC: evaluate gates
        alt gate refuses
            EC-->>OR: refused, with the reason
        else gate allows or is unjudged
            EC-->>OR: ResultSet + Manifest
        end
        OR-->>FE: step.finished
    end

    OR->>OR: run_diagnostics, propose_charts
    OR-->>FE: charts.finished

    opt tier has a Validator
        OR->>VA: review(plan, execution, diagnostics)
        VA-->>OR: verdict
        OR-->>FE: validate.finished
        opt rejected and revisions remain
            OR-->>FE: plan.revising
            OR->>PL: re-plan with the reasons
            opt dataset changed
                OR->>DS: re-resolve
            end
        end
    end

    OR->>NA: write(plan, execution, verdict)
    NA->>NA: check_grounding against all_numeric_values
    NA-->>OR: published, or withheld with a reason
    OR-->>FE: narrate.finished

    OR-->>FE: run.finished (the whole RunOutcome)
    RT->>TR: record_run
    TR->>DB: runs + run_steps + outcome
```

---

## B4. Run state machine

**A run ends `completed`, `blocked` or `failed`. Blocked is not an error.**

```mermaid
stateDiagram-v2
    [*] --> running: POST /runs

    running --> completed: pipeline finished,<br/>narration published
    running --> blocked: pipeline finished,<br/>narration withheld<br/>or every step refused
    running --> failed: an exception anywhere

    completed --> [*]
    blocked --> [*]
    failed --> [*]

    note right of blocked
        A blocked run still returns
        its results. It is not an error.
        It is an honest partial answer.
    end note

    note right of failed
        Comes back readable, naming
        how far it got and why it stopped.
        Never half-written.
    end note
```

---

## B5. Step state machine

**A refused step is a result in its own right, not a failure.**

```mermaid
stateDiagram-v2
    [*] --> pending

    pending --> gate_check: dependencies satisfied
    pending --> skipped: a dependency refused or failed

    gate_check --> ok: gates allow, tool returns
    gate_check --> refused: a gate ran and disagreed
    gate_check --> ok_unjudged: a gate could not be evaluated
    gate_check --> failed: the tool raised

    ok --> [*]
    ok_unjudged --> [*]: runs, and the Validator<br/>is told it was unjudged
    refused --> [*]: a result in its own right
    failed --> [*]
    skipped --> [*]

    note left of refused
        Persisted as status='refused'.
        Surfaces in the canvas beside
        the charts, not behind a tab.
    end note
```

For model calls the same status vocabulary applies. A rejected attempt is a
**step in its own right**, with `attempt = 2` and a parent link, because it
was billed.

---

## B6. Upload lifecycle

**Nothing is ingested until a person confirms the mapping.**

```mermaid
stateDiagram-v2
    [*] --> uploaded: POST /projects/{id}/uploads
    uploaded --> profiled: every column scored<br/>for every role it could play

    profiled --> suggested: a ColumnMapper reorders<br/>the candidates
    profiled --> awaiting_confirmation
    suggested --> awaiting_confirmation

    awaiting_confirmation --> confirmed: POST /uploads/{id}/confirm
    awaiting_confirmation --> refused: filename contains 'synthetic'

    confirmed --> ingested: apply_mapping, then COMMIT
    ingested --> [*]: a Dataset, servable through PriceSource

    refused --> [*]

    note right of suggested
        A model may only REORDER
        candidates the deterministic
        profiler already found admissible.
        A user may pick any role.
    end note

    note right of confirmed
        confirm_mapping is the ONLY
        producer of a mapping that
        apply_mapping will act on.
    end note
```

---

## B7. Re-run and reproduction

**Re-run executes the recorded plan and compares six things per step.**

```mermaid
flowchart TB
    START(["POST /api/runs/{id}/rerun"]) --> LOAD["Load the run"]
    LOAD --> HASPLAN{"outcome has<br/>a plan?"}
    HASPLAN -->|no| C409A(["409: runs that failed before<br/>planning cannot be reproduced"])
    HASPLAN -->|yes| SCOPE["build_project_source<br/><b>scoped to the project</b>"]
    SCOPE --> RESOLVE["Data Steward resolves<br/>the recorded DatasetSpec"]
    RESOLVE -->|DataUnavailableError| C409B(["409: naming what is missing.<br/>An uploaded dataset may<br/>have been deleted"])
    RESOLVE --> EXEC["Econometrician runs<br/>the recorded plan"]
    EXEC --> CMP["Compare, per step"]

    CMP --> D1{"present in<br/>the recording?"}
    CMP --> D2{"same status?"}
    CMP --> D3{"same data<br/>fingerprint?"}
    CMP --> D4{"same params<br/>hash?"}
    CMP --> D5{"same tool<br/>version?"}
    CMP --> D6{"same numbers?"}

    D1 & D2 & D3 & D4 & D5 & D6 --> REPORT["RerunReport:<br/>reproduced, plus the reason<br/>per step where not"]

    style C409A fill:#f8d7da,stroke:#e34948,color:#14181d
    style C409B fill:#f8d7da,stroke:#e34948,color:#14181d
```

**Two facts about re-run are load-bearing.**

1. **It consults no model.** Re-planning would test whether a model repeats
   itself. That is a different question, and the manifest promises nothing
   about it. A test asserts the model call count is unchanged.
2. **The fingerprints agreeing is necessary, not sufficient.** The numbers are
   the thing being reproduced, so they are compared directly with
   `all_numeric_values()`.

An empty run reproduces nothing. Saying `True` for it would be the most
misleading answer available. So `reproduced` is `bool(steps) and all(...)`.

---

## B8. The sandbox process

**The parent owns the wall clock. The child does nothing until it is inside
the job.**

```mermaid
sequenceDiagram
    autonumber
    participant P as Parent (runner.py)
    participant J as Job Object / rlimit
    participant C as Child (child.py)
    participant H as Audit hook

    P->>P: pin BLAS to one thread
    Note over P: unpinned, OpenBLAS on a 24-CPU box<br/>kills `import numpy` inside a 1 GB cap
    P->>C: Popen(sys.executable, stdin=PIPE)
    Note over C: first act is a BLOCKING read of stdin,<br/>so it has nothing to do until it is in the job
    P->>J: AssignProcessToJobObject(pid)
    Note over J: ActiveProcessLimit = 2<br/>(uv's trampoline needs one)
    P->>C: write the payload
    C->>H: register the PEP 578 audit hook
    Note over H: fires from C, cannot be unregistered
    C->>C: install the gated __import__<br/>in the generated code's builtins
    C->>C: exec the generated code
    H-->>C: refuse socket, subprocess, os ops, writes
    C-->>P: __ECONOMETRICA_SANDBOX__ + JSON envelope
    P->>P: enforce the 20s WALL clock (the parent's own)
    P->>J: close, killing anything still inside
```

| Cap | Value | Note |
|---|---|---|
| Memory | 512 MB | Roughly double what the stack needs with BLAS pinned |
| Wall clock | 20 s | **The real timeout**, and it is the parent's |
| CPU | 60 s | A backstop only. A 1 s Job Object cap was measured firing at 5.9, 7.4 and 8.1 s |
| Max output | 4 MB | A result is a handful of estimates; anything near this is a mistake |
| Processes | 2 | Not 1. `uv`'s `sys.executable` is a trampoline |

---

## B9. MCP research loop

**Five conditions gate the loop. Any "no" skips the research phase.**

```mermaid
flowchart TB
    START(["A question"]) --> GATE{"capability on?<br/>servers configured?<br/>allowlist non-empty?<br/>researcher role assigned?<br/>model can call tools?"}
    GATE -->|"any no"| SKIP(["no research phase"])
    GATE -->|"all yes"| CONNECT["McpConnector opens sessions"]
    CONNECT --> LOOP

    subgraph LOOP["Bounded loop, MAX_RESEARCH_ROUNDS = 4"]
        ASK["Ask the model, with the<br/>allowlisted tools available"] --> WANTS{"wants a tool?"}
        WANTS -->|yes| ALLOW{"<b>allowlist check,<br/>BEFORE the server<br/>is asked</b>"}
        ALLOW -->|"exact, server-qualified match"| CALL["call it"]
        ALLOW -->|"no match"| DENY["ToolNotAllowedError.<br/>The server is never told<br/>the tool was wanted"]
        CALL --> ASK
        DENY --> ASK
        WANTS -->|no| DONE
    end

    LOOP --> FINAL["One tool-free call<br/>for a clean summary"]
    DONE --> FINAL
    FINAL --> PLANNER["Planner context"]
    FINAL -.->|"never"| NARRATOR["Narrator"]

    style DENY fill:#f8d7da,stroke:#e34948,color:#14181d
    style NARRATOR fill:#f8d7da,stroke:#e34948,color:#14181d
```

---

## B10. Capability resolution

**A chat may override web search and MCP. Nothing overrides the sandbox
toggle.**

```mermaid
flowchart TB
    subgraph P["Project (NOT NULL, server defaults)"]
        PW["web_search_enabled"]
        PM["mcp_enabled"]
        PS["code_sandbox_enabled"]
        PT["validation_tier"]
    end
    subgraph C["Chat (nullable: null means inherit)"]
        CW["web_search_enabled"]
        CM["mcp_enabled"]
    end

    PW --> RW{"chat value<br/>is null?"}
    CW --> RW
    RW -->|yes| OW["project value"]
    RW -->|no| OW2["chat value"]

    PM --> RM{"chat value<br/>is null?"}
    CM --> RM
    RM -->|yes| OM["project value"]
    RM -->|no| OM2["chat value"]

    PS --> OS["project value only.<br/><b>Not overridable per chat.</b>"]
    PT --> OT["project value"]

    OW & OW2 & OM & OM2 & OS & OT --> RES["ResolvedCapabilities"]
    RES --> BUILD["The router builds ONLY<br/>the collaborators these permit"]
```

**The sandbox is deliberately not chat-overridable.** It is the most
security-sensitive toggle in the system, so it stays at project scope.

**The `None` handling:**

- Project toggles are NOT NULL in the database.
- So `None` is only ever seen on an instance that has not been flushed yet.
- Falling back to the column default keeps the resolver's answer identical
  before and after a flush. It does not leak `None` into a bool field.

---

## B11. Streaming event vocabulary

**Twelve events with dotted names, not a discriminated union.** A client
renders a timeline, and a new phase must not break a client that has not been
updated.

| Event | Payload | Fired |
|---|---|---|
| `run.started` | the question | always, first |
| `run.warning` | prose | zero or more, early. Includes the validator-independence warning |
| `plan.finished` | the whole `AnalysisPlan` | once per plan, including revisions |
| `plan.disagreement` | prose | consensus tier only, where planners differed |
| `data.finished` | `DataQualityReport` | once per resolve, so twice if a revision changed the spec |
| `step.finished` | `StepOutcome` | once per plan step |
| `charts.finished` | the `ChartSpec` list | once per execution pass |
| `validate.finished` | `ValidationVerdict` | once per review |
| `plan.revising` | "revision N of M" | once per revision |
| `narrate.finished` | `Narration` | once |
| `run.failed` | the error | at most once |
| `run.finished` | the whole `RunOutcome` | **always, last**, even after a failure |

`run.finished` firing after `run.failed` is the contract that makes a partial
run readable.

---

## B12. Deployment topology

**One Windows machine: Postgres in Docker on 5433, the API on 8001, Vite on
5173, Ollama on 11434.**

```mermaid
flowchart TB
    subgraph HOST["One Windows machine"]
        subgraph DOCKER["Docker Desktop"]
            PG["econometrica-db<br/>Postgres 16 + TimescaleDB + pgvector<br/>host port <b>5433</b> → container 5432"]
            INIT["infra/initdb/01-extensions.sql<br/><i>runs only on a fresh volume</i>"]
            PG --- INIT
        end

        subgraph APP["Application, started by start.ps1"]
            API["uvicorn, port <b>8001</b>, no --reload"]
            WEB["vite dev, port 5173, bound to ::1<br/>proxying to ECONOMETRICA_API_URL"]
            WEB --> API
        end

        OLLAMA["Ollama, port 11434"]
        STORAGE[("storage/<br/>blobs · price cache · encrypted keys")]

        API --> PG
        API --> OLLAMA
        API --> STORAGE
    end
```

### Ports, and why they are what they are

| Port | Used by | Note |
|---|---|---|
| 5433 | Postgres on the host | Container port is still 5432 |
| **8001** | The API under `start.ps1` | Not 8000. See below |
| 5173 | Vite dev server | Bound to `::1` by default, so open `localhost:5173`, not `127.0.0.1:5173` |
| 8100 | e2e backend, synthetic prices | |
| 8101 | e2e backend, Yahoo prices | Shares one Postgres with 8100 |
| 11434 | Ollama | |

**Port 8000 is not safely ours.**

- A container holding the wildcard address on 8000 answers `127.0.0.1:8000`
  traffic too.
- It wins often enough that uvicorn's own successful bind proves nothing.
- Measured: 25 consecutive health polls returned 404 with `Server: SurrealDB`
  while uvicorn sat bound to `127.0.0.1:8000`.
- The same URL answered 200 from uvicorn minutes later.

**Docker Desktop does not autostart** on this machine. A fresh boot means no
Postgres and roughly 40 test errors.

**Docker Model Runner is disabled** (`"EnableInference": false`). It was
crash-looping Docker Desktop through an orphaned socket.

**Next, 2 minutes:** open [integration patterns](integration-patterns.md) and
read its checklist for adding the next integration.

---

[Documentation](../README.md) · [3. Architecture](README.md) ·
[High-level design](high-level-design.md) · [Low-level design](low-level-design.md) ·
**Blueprints** · [Integration patterns](integration-patterns.md)
