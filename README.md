# Econometrica

Econometrica is a local, GenAI-powered econometrics workbench for financial asset
pricing and market efficiency analysis. A Python FastAPI backend serves a
React/TypeScript frontend, backed by Postgres with TimescaleDB and pgvector. LLM
agents select from a registry of typed, versioned econometric tools — they never
compute statistics themselves.

> **Status.** Phases 0–2 complete; Phase 3 nearly so. Working today: projects and
> chats, the full econometrics core (36 tools across asset pricing, market
> efficiency, volatility, multivariate and event study), five LLM providers
> (Ollama, Anthropic, OpenAI, Gemini, NVIDIA NIM), and a streaming chat pane you
> can hold a real conversation in. Not yet built: the multi-agent orchestration
> that connects the chat to the tools (Phase 4), interactive charts (Phase 5),
> and file upload, telemetry and export (Phase 6).
>
> Working notes for contributors — and for Claude — are in `CLAUDE.md`.

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

Then open <http://127.0.0.1:5173>. Create a project and a chat, pick a provider
and model, and send a message.

> Use `127.0.0.1`, not `localhost`. On Windows `localhost` resolves to `::1`
> first, and if anything else is bound there you get puzzling 404s. The Vite
> proxy already names the address explicitly.

Providers other than Ollama need an API key, stored encrypted at rest:

```bash
curl -X PUT http://127.0.0.1:8000/api/providers/anthropic/key -H "Content-Type: application/json" -d "{\"api_key\":\"sk-ant-...\"}"
```

## Repository layout

```
backend/          FastAPI app, econometric tool registry, LLM provider adapters
  src/econometrica/
    econ/         The tool registry and the five tool families
    llm/          Provider-neutral types plus the five adapters
    api/          Routers
    db/           SQLAlchemy models
  tests/
frontend/         React + TypeScript, three-pane workbench
  src/
  e2e/            Playwright specs
docs/plans/       Design and implementation plans
infra/initdb/     SQL run once on first database startup
docker-compose.yml
```
