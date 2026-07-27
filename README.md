# Econometrica

Econometrica is a local, GenAI-powered econometrics workbench for financial asset
pricing and market efficiency analysis. A Python FastAPI backend serves a
React/TypeScript frontend, backed by Postgres with TimescaleDB and pgvector. LLM
agents select from a registry of typed, versioned econometric tools — they never
compute statistics themselves.

> **Status.** Phases 0–5 complete; Phase 6 in progress.
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
> **And it can be taken away.** `GET /api/runs/{id}/export?format=…` serves the
> run as JSON, Markdown, CSV, XLSX or a ZIP of all of them, and every one
> carries the manifest that reproduces it — the data fingerprint, the tool
> versions, and the source the prices came from. Charts export to PNG and SVG
> from the browser, so the image is the one on screen. **PDF comes from the
> browser's own print pipeline** — a stylesheet, not a dependency in either
> stack: it forces light surfaces whatever theme you were reading in, keeps a
> chart card whole across a page break, and always prints the provenance block,
> because an exported artifact that cannot be traced back is what this project
> exists not to produce.
>
> The end-to-end gate runs it against a live local model. In a typical pass an
> 8B model plans five steps, four run, and **GARCH is refused** because the
> data has no ARCH effects to model.
>
> **Real market data works.** `ECONOMETRICA_PRICE_SOURCE=yahoo` fetches
> dividend-adjusted daily closes through yfinance, cached on disk so a run, its
> re-run and its exports share one fetch. The source and its adjustment policy
> are named in every quality report, because Yahoo's split-adjusted and
> dividend-adjusted closes for the same day can differ by 3% and reproducing a
> number means knowing which one produced it.
>
> An analysis can also ask for a **risk-free rate** (any of seventeen FRED
> series, converted to the frame's own frequency) and a **Fama-French factor
> set** — `ff3`, `ff5` or `carhart4` — fetched from Ken French's data library.
> With those, all 37 tools are reachable: AAPL against the three-factor set over
> 2018–2023 gives a market loading of 1.30 with negative size and value
> loadings, which is what a large-cap growth stock should look like.
>
> **A CSV, XLSX or Parquet file can be analysed the same way.** An upload is
> profiled, every column scored for the roles it *could* play, and the mapping
> confirmed by a person before anything is stored — a model may only reorder
> candidates the profiler already found admissible, and only a confirmed
> mapping is ever ingested. The observations land in a Timescale hypertable and
> are served through the same `PriceSource` protocol as Yahoo, so nothing above
> that seam knows whether a series was fetched or uploaded.
>
> `ECONOMETRICA_PRICE_SOURCE=synthetic` still generates reproducible random
> walks so the pipeline runs with no network at all; it is never the default, it
> is not going away, and every run built on it carries a `synthetic_data` risk
> flag. Left unset, a run refuses with an explanation rather than inventing data.
>
> Every run records its own trace — a DAG of model calls and tool
> invocations with tokens, latency and parent links, readable at
> `GET /api/runs/{id}`. Rejected attempts are steps in their own right,
> because they were billed.
>
> **When no tool fits, a model may write code** — the escape hatch §2 of the
> design chose, built last and off by default. It runs in a separate process
> with no network, no filesystem to speak of, an import allowlist and
> OS-enforced memory, CPU and wall-clock caps, and every restriction has a test
> that tries to get out of it. Three conditions gate it: the project must
> enable it (a chat cannot), the Validator must sign off, and the result is
> **marked `unvalidated` everywhere it surfaces** — in the manifest, in the run
> banner and in the printed provenance. That marking is the point. A live probe
> found a real local model producing a plausible, cleanly-running and badly
> wrong formula one run in five; nothing in a sandbox can catch that, which is
> why the number never gets to look like one a tested tool produced.
>
> **In progress:** Phase 6 — one task left, a full-stack end-to-end regression
> on real data.
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
press Run analysis. A run needs a data source, or it refuses rather than
inventing one — start the backend with `ECONOMETRICA_PRICE_SOURCE=yahoo` for
real prices, or `=synthetic` to work offline on generated data that every
report flags as such.

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
