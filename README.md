# Econometrica

Econometrica is a local, GenAI-powered econometrics workbench for financial asset
pricing and market efficiency analysis. A Python FastAPI backend serves a
React/TypeScript frontend, backed by Postgres with TimescaleDB and pgvector. LLM
agents select from a registry of typed, versioned econometric tools — they never
compute statistics themselves.

> **Status.** Phases 0–4 complete; Phase 5 is five tasks in, through the
> artifact canvas.
>
> **Working today:** projects and chats; the full econometrics core (37 tools
> across asset pricing, market efficiency, volatility, multivariate and event
> study); five LLM providers (Ollama, Anthropic, OpenAI, Gemini, NVIDIA NIM);
> a streaming chat pane you can hold a real conversation in; the
> multi-agent pipeline behind `POST /api/chats/{id}/runs` — Planner, Data
> Steward, Econometrician, Validator and Narrator, with executable tool
> preconditions and a numeric grounding gate; a renderer for each of the
> fourteen chart types a result can imply, themed light and dark, each with a
> table view of the same numbers; and **a canvas that runs an analysis and
> shows what came back** — charts in tabs, pinnable and full-screenable, with
> the refusals, the unjudged checks and the data-quality risks kept on screen
> beside them rather than behind a tab.
>
> **A result can be re-run from its manifest.** `POST /api/runs/{id}/rerun`
> re-executes the recorded plan against freshly resolved data and reports
> whether the numbers came back the same, naming the step and the reason when
> they did not. It asks no model anything.
>
> The end-to-end gate runs it against a live local model. In a typical pass an
> 8B model plans five steps, four run, and **GARCH is refused** because the
> data has no ARCH effects to model.
>
> **Real market data is Phase 6.** Until those adapters land,
> `ECONOMETRICA_PRICE_SOURCE=synthetic` generates reproducible random walks so
> the pipeline can be run and demonstrated; it is never the default, and every
> run built on it carries a `synthetic_data` risk flag. Left unset, a run
> refuses with an explanation rather than inventing data.
>
> Every run records its own trace — a DAG of model calls and tool
> invocations with tokens, latency and parent links, readable at
> `GET /api/runs/{id}`. Rejected attempts are steps in their own right,
> because they were billed.
>
> **Not started:** exports (the rest of Phase 5), and uploads, telemetry and
> MCP (Phase 6).
>
> Working notes for contributors — and for Claude — are in `CLAUDE.md`. The
> design and phase plans are in `docs/plans/`.

## Prerequisites

- Docker (Docker Desktop on Windows) — runs the Postgres/TimescaleDB/pgvector stack
- [uv](https://docs.astral.sh/uv/) — manages the Python 3.12 toolchain and virtualenv
- Node.js 20+ and npm — for the frontend
- Optionally [Ollama](https://ollama.com/) — the one provider that needs no API key,
  and the quickest way to see the chat working

Python 3.12 is required and pinned via `requires-python` in `backend/pyproject.toml`;
`uv` downloads it automatically, so no system Python 3.12 install is needed.

## Quickstart

### 1. Configure the environment

```bash
cp .env.example .env
```

The defaults work for local development. `.env` is gitignored.

### 2. Start the database

```bash
docker compose up -d db
```

This starts `econometrica-db` on host port `5433` (mapped to container port 5432)
and, on a **fresh** volume, runs `infra/initdb/01-extensions.sql` to enable the
`timescaledb` and `vector` extensions in both the `econometrica` and
`econometrica_test` databases.

Verify the extensions loaded:

```bash
docker exec econometrica-db psql -U econometrica -d econometrica -c "SELECT extname FROM pg_extension;"
docker exec econometrica-db psql -U econometrica -d econometrica_test -c "SELECT extname FROM pg_extension;"
```

Both should list `timescaledb` and `vector`.

> The init scripts only run when the data volume is empty. If you change them,
> reset the volume first with `docker compose down -v`.

### 3. Install backend dependencies

```bash
cd backend
uv sync --extra dev
```

### 4. Run the tests

```bash
cd backend
uv run pytest
```

Around 40 of these need the database from step 2. Tests marked `live` talk to a
real Ollama daemon and skip when one is absent — a mock only ever proves the
adapter matches what we *believe* the wire format is, which is the assumption
worth checking.

The rest of the gate, all of which CI-equivalent work should pass:

```bash
cd backend && uv run ruff check src tests alembic && uv run mypy src
```

```bash
cd frontend && npx vitest run && npx tsc --noEmit
```

### 5. Install the frontend and run it

```bash
cd frontend
npm install
```

Start the API and the dev server in two terminals:

```bash
cd backend && uv run uvicorn econometrica.main:app --port 8000 --host 127.0.0.1
```

```bash
cd frontend && npm run dev
```

Then open <http://localhost:5173>. Create a project and a chat, pick a provider
and model, and send a message.

To run an *analysis* rather than a conversation, use the canvas in the middle
pane: type a question, choose the model that should plan and narrate it, and
press Run analysis. Start the backend with `ECONOMETRICA_PRICE_SOURCE=synthetic`
first, or the run will refuse for want of data — see the note above about what
that source is and is not.

Every chart type is also rendered over fixture data at
<http://localhost:5173/gallery.html>, which is the quickest way to see them all
in either theme. It is a dev harness only; `vite build` takes `index.html`
alone, so it never ships.

> **The two servers want different addresses, and it is not arbitrary.**
>
> Open the **app** at `localhost:5173`. Left at its default host, Vite binds
> only the first address the OS resolves — `::1` on Windows — so
> `127.0.0.1:5173` is refused outright. Pass `--host 127.0.0.1` if you want it
> reachable there too, as `playwright.config.ts` does.
>
> Point everything at the **API** at `127.0.0.1:8000`, never `localhost:8000`.
> `localhost` resolves to `::1` first, and anything else bound there answers
> instead of uvicorn — which returns puzzling 404s rather than a connection
> error. `vite.config.ts` already names the address explicitly, and
> `--host 127.0.0.1` above is what makes uvicorn own it.

Providers other than Ollama need an API key, stored encrypted at rest:

```bash
curl -X PUT http://127.0.0.1:8000/api/providers/anthropic/key -H "Content-Type: application/json" -d "{\"api_key\":\"sk-ant-...\"}"
```

## Repository layout

```
backend/          FastAPI app, econometric tool registry, LLM provider adapters
  src/econometrica/
    econ/         The tool registry and the five tool families
      diagnostics/  Deterministic assumption checks, run before any verdict
      gates.py      Executable tool preconditions — refusals, not advice
    llm/          Provider-neutral types plus the five adapters
    agents/       The six roles, the orchestrator, and the grounding gate
    api/          Routers
    db/           SQLAlchemy models
  tests/
frontend/         React + TypeScript, three-pane workbench
  src/
    components/charts/  One renderer per chart spec type, plus the palette
    components/canvas/  The artifact canvas: runs, charts, findings, re-run
  gallery.html    Dev-only: every chart type over fixture data
  e2e/            Playwright specs
docs/plans/       Design and implementation plans
infra/initdb/     SQL run once on first database startup
docker-compose.yml
```

## How it avoids making numbers up

Three mechanisms, each testable and none of them a prompt:

- **LLMs never compute statistics.** They select from `econ/registry.py`; the
  tools compute. Every number traces to a `ResultSet` with a reproducibility
  manifest.
- **Tools refuse work the data cannot support.** A `Gate` is checked against
  the real series before a tool runs — fitting a GARCH to a series with no
  ARCH effects is declined, with the reason, rather than returning a
  persistence figure a reader would take seriously.
- **Prose is checked against the results.** Every number a Narrator writes is
  matched to a computed one. Unmatched figures are not edited out; the whole
  interpretation is withheld and the results are returned without it.
