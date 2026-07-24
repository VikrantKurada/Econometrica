# Econometrica

Econometrica is a local, GenAI-powered econometrics workbench for financial asset
pricing and market efficiency analysis. A Python FastAPI backend serves a
React/TypeScript frontend, backed by Postgres with TimescaleDB and pgvector. LLM
agents select from a registry of typed, versioned econometric tools — they never
compute statistics themselves.

> Status: early scaffold. Only the repository layout, the database stack, and the
> Python package skeleton exist so far.

## Prerequisites

- Docker (Docker Desktop on Windows) — runs the Postgres/TimescaleDB/pgvector stack
- [uv](https://docs.astral.sh/uv/) — manages the Python 3.12 toolchain and virtualenv
- Node.js 20+ and npm — for the frontend (not yet scaffolded)

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

## Repository layout

```
backend/          Python package (FastAPI app, econometric tool registry)
  src/econometrica/
  tests/
docs/plans/       Design and implementation plans
infra/initdb/     SQL run once on first database startup
docker-compose.yml
```
