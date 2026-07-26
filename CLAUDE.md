# Econometrica — working notes for Claude

A local, GenAI-powered econometrics workbench for financial asset pricing and
market efficiency analysis. Python/FastAPI backend, React/TypeScript frontend,
Postgres + TimescaleDB + pgvector.

**Read `docs/plans/2026-07-24-econometrica-design.md` for the approved design
and `docs/plans/2026-07-24-econometrica-implementation.md` for the phase plan.**
Phases 1–2 are specified there step by step; 3–6 at task level. Each phase gets
its own step-level document when it is reached — Phase 4's is
`docs/plans/2026-07-25-econometrica-phase-4.md`, Phase 5's is
`docs/plans/2026-07-25-econometrica-phase-5.md`.

---

## The one invariant

**LLMs never compute statistics.** They select from a registry of ~37 typed,
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
| 2 — econometrics core (37 tools, 5 families) | done, phase gate green, 97% coverage |
| 3 — LLM providers + streaming chat | done, e2e gate green |
| 4 — multi-agent orchestration | done, e2e gate green |
| 5 — charts and artifact canvas | 5.0–5.4 done; 5.5 next |
| 6 — telemetry, uploads, MCP, exports | not started |

**892 backend tests, 235 frontend tests, 4 Playwright e2e.** ruff and
`mypy --strict` clean on `src`. `alembic check` reports no drift.

### The immediate next task

**Task 5.5 — exports.** PNG, SVG, PDF, CSV, XLSX, JSON, Markdown, and a
project ZIP. Every export embeds the reproducibility manifest or ships beside
it: an exported chart that cannot be traced back is exactly what this project
exists not to produce.

Most of what it needs now exists. `GET /api/runs/{id}` returns the whole
`RunOutcome` — plan, results, charts, quality, narration — so an export reads
one row rather than replaying a run. The chart specs are typed on both sides,
and `buildFigure(spec, result, theme)` in `components/charts/figure.ts` is a
pure function, so a headless render needs no React.

**Re-run reproduces**, verified end to end through the UI against a live
model, which closes the parent plan's last definition-of-done item.

Two things worth knowing before starting:

- **The Phase 4 e2e gate is model-dependent, not reliably green.**
  `analysis.spec.ts:227` asserts that an unpublished narration always carries
  grounding issues — but when the Validator refuses there is nothing to
  narrate and no issues to report. It fails on those runs and passes on
  others, and it was failing before Task 5.3 too (verified by stashing). The
  third path is simply uncovered.
- **`ECONOMETRICA_PRICE_SOURCE=synthetic`** makes the whole pipeline runnable
  without market data (Phase 6 owns the real adapters), and it is genuinely
  reproducible — the seed is a hash of the ticker, so re-running a manifest
  gets the same series back. Any run using it carries a `synthetic_data` risk
  flag, which the canvas shows as an alert no tab can hide.

Phase 4 is the interesting one: six agent roles, the deterministic
`DiagnosticsEngine` (already built, `econ/diagnostics/`) feeding a Validator on
a *different provider*, and the numeric grounding gate that blocks any number
in narrator prose that is not in `ResultSet.all_numeric_values()`.

### Runs and the canvas

- **A run's artifacts live in `runs.outcome`**, a JSONB column holding the
  whole serialised `RunOutcome`. `RunDetail` returns it; `RunRead` does not,
  and must not — a result's series are in there, so listing runs would drag
  every one of them along. Steps say what a run *did*; the outcome says what
  it *produced*.
- **Re-run consults no model.** `POST /api/runs/{id}/rerun` re-executes the
  recorded plan against freshly resolved data and compares manifests and
  numbers. Re-planning would test whether a model repeats itself, which the
  manifest promises nothing about; a test asserts the call count is unchanged.
- **Re-resolve the dataset when a revision changes it.** Resolving once before
  the revision loop meant a revised plan ran on the previous plan's frame, so
  the recorded `plan.dataset` described data the results did not come from.
  Re-run found it. Unchanged specs are not re-fetched.
- **Several backend conveniences never cross the wire** — `ExecutionReport.
  results`, `.refusals`, `PreconditionVerdict.refused` are Python properties.
  `components/canvas/artifacts.ts` restates the rules once for the client.
- **`getByLabel` matches on substring.** The canvas's "Analysis model" picker
  silently broke `chat.spec.ts`'s `getByLabel("Model")`; e2e locators for
  short labels need `{ exact: true }`.

### Charts

- **Load the `dataviz` skill before touching chart code.** The palette, the
  caps and the mark specs all come from it.
- **The chart card is `bg-surface-1`** — `#fafafa` light, `#121416` dark. Those
  are the surfaces the palette was validated against, so a card on
  `surface-0` would make the recorded contrast a number about a different
  screen. Re-run the validator if either token moves.
- **`--series-1…8` are hex while every neighbouring token is oklch.** They are
  the exact steps the validator was run on; converting them rounds the values
  the colour-blindness separations were measured from. `palette.test.ts`
  asserts the CSS and the TypeScript fallback agree.
- **Nothing above the tool boundary fits anything.** A scatter's fit line is
  drawn from the result's own intercept and slope estimates, or not at all.
- **Plotly needs `global`.** Its CommonJS build reaches for the Node global, so
  `vite.config.ts` defines it as `globalThis`; without that the charts throw on
  first import. The bundle is `lib/core` plus four traces, not the ~3 MB whole.
- **Vitest runs with `css: false`**, which stubs stylesheets *including*
  `?raw` to `""`. A test that needs the stylesheet's text reads it off disk.

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

## Permissions

This project runs in **Allow All** — `permissions.defaultMode` is set to
`bypassPermissions`, so tool calls execute without confirmation prompts.

**Where it lives:** `.claude/settings.local.json`, alongside ~125 accumulated
allow rules. That file is **gitignored**, because a permission posture is a
personal choice about this machine, not something a clone should silently
inherit. This file cannot set it — `CLAUDE.md` is instructions to Claude, and
permission mode is harness configuration; writing "allow all" here would have
no mechanical effect.

**To restore it on a fresh clone**, create `.claude/settings.local.json`:

```json
{
  "permissions": {
    "defaultMode": "bypassPermissions",
    "allow": []
  }
}
```

**What it means in practice.** Nothing asks first — file writes, shell
commands, network calls, `git push`. That is the point, and it is why this
project's other conventions matter more than they otherwise would: verify
before destructive commands, prefer additive fixes, and check `git status`
before assuming the tree is clean. If you ever want the confirmation layer
back for one session, start with `--permission-mode default`; to retire it
entirely, change `defaultMode` to `"default"` in that file.

The allow list is worth keeping even under Allow All: it is what still applies
if the mode is ever turned off, and it survives as a record of what has been
sanctioned.

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
- **The Phase 3 e2e needs a live Ollama and a small chat model.** `chat.spec.ts`
  sends a real prompt to a real model. It prefers `tinyllama` and falls back to
  whatever else streams — which on this machine means a 40 GB model, so keep a
  small one pulled. `E2E_OLLAMA_MODEL` overrides the choice. With Ollama down
  the spec **skips rather than fails**, so read the report, not just the exit
  code.
- **Python is pinned to 3.12** (`requires-python = ">=3.12,<3.13"`). The system
  has 3.14, but `arch` / `numba` / `linearmodels` publish no 3.14 wheels.
- **pandas 3.0.5 is fine.** All 21 econometric paths were probed against it. Do
  not "fix" this by pinning back to 2.x. One sharp edge: it **rejects the
  `M`/`Q`/`A` resample aliases outright** — `resample("M")` raises
  `ValueError`, it does not warn — while `DatasetSpec.frequency` and
  `econ.returns.PERIODS_PER_YEAR` still speak those letters. Map to
  `ME`/`QE`/`YE` at the boundary, as `agents/data_steward.py` does.
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
- **Ollama capabilities come from `/api/show`, not `/api/tags`.** Tags reports
  neither context length nor tool support, and guessing them from the model
  name was wrong both ways — on this machine 6 of 13 chat models cannot call
  tools, and real windows run 512 to 262144, not the 8192 the adapter used to
  claim for everything. The context key is architecture-prefixed, so read
  `general.architecture` to name it: matching `*.context_length` alone also
  catches `mistral3.rope.scaling.original_context_length`, a smaller number.
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
- Alembic's autogenerate **does emit CHECK constraints when it creates a
  table** — the `runs`/`run_steps` revision carries all thirteen. What it
  cannot see is one added to or changed on a table that already *exists*: that
  revision comes out empty and has to be hand-written, as
  `1e6846482bc2_validation_tier_check_constraint.py` was. `alembic check`
  verifies neither case, so tests are the only gate —
  `tests/db/test_run_model.py` exercises each constraint against Postgres, and
  `tests/db/test_migrations.py` asserts every constraint in the models reaches
  some migration at all.

### The tool registry

- **Tools register as an import side-effect** of the five `econ/<family>/`
  packages. `econ.load_tools()` is the one place that imports them all;
  `main.py` calls it. Anything resolving a tool *by name* needs it first.
- **A test asserting the registry is populated must run in a subprocess.**
  Every module under `tests/econ` imports the family it exercises, so by
  collection time the in-process registry is full no matter what the
  application does — which is how it stayed empty in a live server until
  Phase 4. See `tests/api/test_app_startup.py`.

---

## Starting a new session

Say what you want next; this file loads automatically. A good opener:

> Continue Econometrica. Read CLAUDE.md and
> `docs/plans/2026-07-25-econometrica-phase-5.md`, then do Task 5.5 (exports).

Task lists do **not** survive across sessions — this file and the plan document
are the memory. Update the "Where things stand" table when a phase moves.

**Keep `README.md` current in the same breath.** It is the one file a visitor
reads first, and it drifts silently because nothing tests it. When a task
lands, check its Status block, its repository layout, and any instruction the
change makes untrue — the "open the app at `localhost:5173`" line was wrong for
months because `npm run dev` binds `::1` only, and no test could have caught it.
