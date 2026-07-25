# Econometrica — working notes for Claude

A local, GenAI-powered econometrics workbench for financial asset pricing and
market efficiency analysis. Python/FastAPI backend, React/TypeScript frontend,
Postgres + TimescaleDB + pgvector.

**Read `docs/plans/2026-07-24-econometrica-design.md` for the approved design
and `docs/plans/2026-07-24-econometrica-implementation.md` for the phase plan.**
Phases 1–2 are specified there step by step; 3–6 at task level.

---

## The one invariant

**LLMs never compute statistics.** They select from a registry of ~36 typed,
versioned econometric tools; the tools compute. Every number a user sees traces
to a `ResultSet` produced by a tested function with a reproducibility manifest.

Consequences that keep coming up:

- statsmodels / arch / linearmodels result objects never leave a tool module.
  Everything above the tool boundary speaks `econ.types.ResultSet`.
- Vendor SDK types never leave a provider adapter. Everything above speaks
  `llm.types`.
- `Diagnostic.passed` is tri-state. `None` means "not judged", never "failed".

---

## Where things stand

| Phase | State |
|---|---|
| 0 — scaffold | done |
| 1 — DB, API, three-pane shell | done |
| 2 — econometrics core (36 tools, 5 families) | done, phase gate green, 97% coverage |
| 3 — LLM providers + streaming chat | 9 of 10 tasks done |
| 4 — multi-agent orchestration | not started |
| 5 — charts and artifact canvas | not started |
| 6 — telemetry, uploads, MCP, exports | not started |

**629 backend tests, 64 frontend tests, 1 Playwright e2e.** ruff and
`mypy --strict` clean on `src`. `alembic check` reports no drift.

### The immediate next task

**Task 3.10 — Phase 3 e2e.** `frontend/e2e/` has `skeleton.spec.ts` (Phase 1)
and `playwright.config.ts` already wired for two servers. Add a spec that
selects Ollama plus a model, sends a message, and asserts a streamed reply
appears and survives a reload. Then Phase 3 is closed and Phase 4 begins.

Phase 4 is the interesting one: six agent roles, the deterministic
`DiagnosticsEngine` (already built, `econ/diagnostics/`) feeding a Validator on
a *different provider*, and the numeric grounding gate that blocks any number
in narrator prose that is not in `ResultSet.all_numeric_values()`.

---

## Commands

Backend, from `backend/` — everything runs under `uv run`, there is no venv to
activate:

```bash
uv run pytest -q
```

```bash
uv run ruff check src tests alembic
```

```bash
uv run mypy src
```

```bash
uv run alembic upgrade head
```

Frontend, from `frontend/`:

```bash
npx vitest run
```

```bash
npx tsc --noEmit
```

```bash
npm run test:e2e
```

Database (needed by ~40 backend tests):

```bash
docker compose up -d db --wait
```

---

## Environment gotchas

These cost real time when rediscovered. All are verified on this machine.

- **Docker Desktop does not autostart** (`AutoStart: false`), so a fresh boot
  means no Postgres and ~40 test errors. Start it, then `docker compose up -d db`.
- **Docker Model Runner is disabled** (`"EnableInference": false` in
  `%APPDATA%\Docker\settings-store.json`). It was crash-looping Docker Desktop
  via an orphaned socket. Leave it off unless you want that fight back.
- **Port 8000 is contested.** An `opennotebook-surrealdb` container holds
  `0.0.0.0:8000`. `127.0.0.1:8000` reaches our uvicorn; **`localhost:8000`
  resolves to `::1` and hits SurrealDB instead**, returning confusing 404s.
  Always name `127.0.0.1`. The Vite proxy and the Playwright config already do,
  and e2e uses port 8100 to sidestep it entirely.
- **Python is pinned to 3.12** (`requires-python = ">=3.12,<3.13"`). The system
  has 3.14, but `arch` / `numba` / `linearmodels` publish no 3.14 wheels.
- **pandas 3.0.5 is fine.** All 21 econometric paths were probed against it. Do
  not "fix" this by pinning back to 2.x.
- **PowerShell mangles `git commit -m`** when the message contains double
  quotes — it re-parses before handing off to git, silently turning part of the
  message into a pathspec. Write the message to a file and use `git commit -F`.
- **`git` writes progress to stderr**, which PowerShell surfaces as
  `NativeCommandError`. A push that prints `* [new branch]` succeeded.

---

## Conventions in force

- **TDD, strictly.** Write the failing test, run it, watch it fail with the
  expected error, then implement. Several real bugs in this codebase were found
  only because a test was run red first.
- **Verify against reality, not just mocks.** Mocks prove an adapter matches
  what we *believe* a wire format is. Live probes against the real Ollama daemon
  have caught three separate wrong beliefs so far. There are `@pytest.mark.live`
  tests that skip when the service is absent.
- **Comments explain constraints, not mechanics.** Say why a choice was forced,
  not what the next line does.
- **Conventional Commits**, with the *reasoning* in the body.
- **Branch:** all work is on `feat/foundation`; `main` tracks it. Remote is
  `https://github.com/VikrantKurada/Econometrica` (private).

### Provider adapters

`llm/registry.py` is the one place that knows every provider. Adding one means
adding a `ProviderSpec` and a factory.

- Ollama, OpenAI, NVIDIA, Gemini use **httpx**.
- **Anthropic uses the official `anthropic` SDK** — required by the `claude-api`
  skill, which you should load before touching that adapter.
- **Load the `claude-api` skill for any Anthropic/Claude API work.** It carries
  current model IDs and API drift that training data gets wrong. Example: Opus 5,
  Fable 5, Sonnet 5, Opus 4.8/4.7 **reject `temperature` with a 400** — the
  adapter drops it for those models (`NO_SAMPLING_PARAMS`).
- **Load the `dataviz` skill before writing any chart code** in Phase 5.

### Database

- Order transcripts on `Message.seq` (a Postgres identity column), **never on
  `created_at`** — that is the transaction timestamp, so rows written together
  tie exactly.
- Every NOT NULL column pairs a Python `default` with a matching
  `server_default`, so non-ORM inserts land valid rows.
- Alembic **does not autogenerate CHECK constraints**. Hand-write them, and know
  that `alembic check` cannot verify them either — a test has to.

---

## Starting a new session

Say what you want next; this file loads automatically. A good opener:

> Continue Econometrica. Read CLAUDE.md and the implementation plan, then do
> Task 3.10 (Phase 3 e2e).

Task lists do **not** survive across sessions — this file and the plan document
are the memory. Update the "Where things stand" table when a phase moves.
