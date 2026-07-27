# Econometrica — working notes for Claude

A local, GenAI-powered econometrics workbench for financial asset pricing and
market efficiency analysis. Python/FastAPI backend, React/TypeScript frontend,
Postgres + TimescaleDB + pgvector.

**Read `docs/plans/2026-07-24-econometrica-design.md` for the approved design
and `docs/plans/2026-07-24-econometrica-implementation.md` for the phase plan.**
Phases 1–2 are specified there step by step; 3–6 at task level. Each phase gets
its own step-level document when it is reached — Phase 4's is
`docs/plans/2026-07-25-econometrica-phase-4.md`, Phase 5's is
`docs/plans/2026-07-25-econometrica-phase-5.md`, Phase 6's is
`docs/plans/2026-07-27-econometrica-phase-6.md`.

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
| 5 — charts and artifact canvas | done, e2e gate green |
| 6 — real data, uploads, telemetry, MCP | in progress — 6.11 of 16 tasks |

**1264 backend tests, 282 frontend tests, 5 Playwright e2e.** ruff and
`mypy --strict` clean on `src`. `alembic check` reports no drift.

### The immediate next task

**Phase 6, Task 6.12** — project-scoped document retrieval over pgvector. Read
`docs/plans/2026-07-27-econometrica-phase-6.md`; it carries all sixteen tasks,
six decisions, and the live-probe findings below.

**The MCP allowlist is default-deny and has no wildcards.** `files:*` is a
literal tool name, not a pattern — a pattern would re-admit whatever a server
added next. Matching is exact and server-qualified, since `files:read` and
`shell:read` are different tools. **The gate runs before the session is asked**,
so a refused tool is never named to the server, and it is read per call so an
allowlist change needs no restart. Discovery lists everything a server offers
marked `allowed`, because that is how a user builds the list — listing is never
permitting.

`mcp==1.28.1` is a dependency now. Its in-memory transport
(`create_connected_server_and_client_session`) is what lets the tests drive a
**real** server rather than a mock — the proof an unlisted tool never ran is the
server's own execution log.

**A step now records its prompt and response.** §8 asked for them from the
start and nothing captured them until 6.10, so a trace could name the model but
not the decision. `AgentResult.prompts` pairs one prompt per attempt — a retry
is a different conversation — and both are truncated at
`agents.base.PROMPT_LIMIT`, because the Planner's prompt carries the whole tool
catalogue.

**The canvas Trace tab is a DAG, not a table.** `TraceGraph` nests children
under `parent_id` so a retry reads as a second attempt rather than as new work;
a step whose parent is missing shows at the root, since `parent_id` is
`ON DELETE SET NULL`. The Cost tab renders `GET /api/metrics`, fetched only when
opened.

**Telemetry is not a second run trace.** `run_steps` records every model call
with its agent, provider, tokens and cost — that stays. Spans cover what it
cannot see: HTTP handlers, database timings, transport. **No number is summed
from both**, and `spans` has no token or cost column at all so there is nothing
to populate by mistake. `GET /api/metrics` reads latencies from spans and tokens
from steps.

**`span()` is inert until configured** and swallows sink failures, because
telemetry may never break what it measures. The tracer provider is deliberately
*not* registered globally — that can only happen once per process, which would
make a batch exporter impossible to shut down. OTLP is off unless
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, and its export timeout is 2s so an
unreachable collector cannot hold a shutdown open.

**Uploads work end to end.** A file is profiled, its mapping confirmed by a
person, and its observations stored in `observations` — **the project's first
Timescale hypertable**. `UploadedPriceSource` then serves it through the same
`PriceSource` protocol as Yahoo, so nothing above the protocol knows uploads
exist. Verified on a real two-ticker export: 1506 observations stored, 36
monthly rows resolved, `capm` beta 0.812.

**Three things about the hypertable that alembic cannot see.** The conversion
itself is invisible to autogenerate, so `create_hypertable` is hand-written in
the migration and asserted against Timescale's catalogue in a test. It creates
its own `observations_ts_idx`, which made `alembic check` want to drop an index
on every run — so `create_default_indexes => FALSE` and we declare
`ix_observations_ts` ourselves. And `field` is part of the primary key: a wide
file mapping both a close and a volume has two rows per (ts, symbol).

**The `ColumnMapping` screen is still only in `/gallery.html`.** The backend can
honour an upload fully now; what remains is deciding where "Data" lives in the
three-pane layout.

**Uploads profile, propose and confirm — but store nothing analysable yet.**
`POST /api/projects/{id}/uploads` returns a profile plus a suggested mapping;
`POST /api/uploads/{id}/confirm` validates it and reports what it would ingest.
**`confirm_mapping` is the only thing that produces a mapping `apply_mapping`
will act on**, so a model's suggestion cannot be acted on by construction.

**The user and the model are constrained differently on purpose.** A user may
choose a role the profiler never suggested; a model may only pick among the
candidates it did. The `ColumnMapping` screen lives in `/gallery.html` rather
than the app until 6.8 makes an uploaded file runnable — a button promising an
upload the backend cannot yet analyse would be worse than no button.

**`services/ingest.py` profiles an upload and never decides.** It scores every
role a column *could* play — `date`, `ticker`, `price`, `return`, `volume`,
`factor`, `ignore` — and 6.7's model may only reorder candidates the profiler
already found admissible. Deterministic, like the Data Steward and
`charts/propose.py`.

**Never use `read_csv(sep=None)`.** It delegates to `csv.Sniffer`, which picks
from the whole alphabet: on a one-column file holding `price` it split on the
`r` and returned columns `p` and `ice`. The delimiter comes from a closed set
now. That also settles the comma: `1,200` is ambiguous in isolation, but a file
using commas for decimals cannot also use them as separators, so a
comma-delimited file means thousands and any other delimiter admits decimals.

**The grounding gate no longer withholds narrations over their own citations.**
`check_grounding` takes `step_ids` and exempts a number only when the letters
before it plus its digits spell an id the plan actually contains — so `(s3)`
passes and `(s7)` does not. It also exempts `YYYY–YYYY` year ranges, found by
running a real narration: a model titled its answer `(2020–2024)` and the gate
read both years as fabrications. **The tolerance did not move** — the `-15.066`
case still fails, and its test sits beside the exemption so nobody loosens it.

**All 37 tools are now reachable.** `ff3`, `ff5` and `carhart4` run on real Ken
French factors — `DatasetSpec.factors` names a set and the Data Steward joins
its columns under the tools' own parameter names. **A factor set brings its own
`risk_free`**, because these factors are excess returns against Ken French's RF;
asking for a FRED rate as well is honoured but raises `mixed_risk_free`.
`carhart4` is **monthly-only**: pandas-datareader 0.11.1 raises `TypeError` on
`F-F_Momentum_Factor_daily` at every date range.

**Values are percent and the index is a `Period`** — both silently wrong if
missed, so `data/famafrench.py` converts at the boundary and both have their own
test. Factors are reindexed onto the price calendar, never forward-filled: a
factor return belongs to its own period.

**Real market data works, end to end.** `ECONOMETRICA_PRICE_SOURCE=yahoo`
fetches dividend-adjusted closes; `DatasetSpec.risk_free` resolves a FRED
treasury series into a per-period `risk_free` column; and
`tests/data/test_live_integration.py` drives both through the Data Steward into
`capm` — AAPL against the S&P 500, 2018–2023 monthly, beta 1.273, no quality
flags. That file is the place to extend when a new source lands: the adapters
each have their own tests, but only that one checks the composition.

`data/registry.py` is the one place that knows every source — add a `SourceSpec`
and a factory, as `llm/registry.py` does for providers — and it decides which
sources are wrapped in the cache. `data/base.py` holds `PriceSource` and
`DataUnavailableError`; **import them from there, never from
`agents/data_steward`**, which only re-exports them. `data/` is the lower layer
and importing upward is a cycle the moment the steward needs to call down —
`tests/data/test_layering.py` enforces it, including a subprocess check of both
import orders, because in-process both modules are already in `sys.modules` by
collection time.

**Rates are not prices.** `data/rates.py` declares a convention per series id
rather than inferring a scale from magnitude, and de-annualises by compounding —
`(1+r)^(1/n)-1`, not `r/n` — to match Ken French's own definition of `RF`. An
unlisted series raises instead of guessing. The rate goes into the data
fingerprint, because a CAPM on excess returns and one on raw returns are
different analyses.

**Cache entries expire, and that is a correctness property rather than
housekeeping.** A vendor recomputes adjusted closes on every split and
dividend, so a stale entry is a *different series* and a re-run that
"reproduced" from one would be reporting on the cache. Default max age is one
day. When an entry is stale **and** the source is unreachable, the cache
raises rather than serving it — there is no channel to disclose staleness,
because `DataQualityReport.source` is read from `label` before the fetch.
Offline-friendliness comes from consulting the cache *before* the source, not
from standing in for it.

**Phase 5 is closed.** Re-run reproduces a result from its manifest, verified
through the UI against a live model, which was the last open item in the parent
plan's definition of done.

### What the market-data probes found (2026-07-27)

The parent plan's version floors are two years stale and **four of its five
assumptions about market data are wrong**. All verified against the real
services on this machine.

- **Stooq is unreachable, and it is not a wiring problem.**
  `pandas-datareader` resolved to **0.11.1**, which implements only
  `bankofcanada`, `econdb`, `eurostat`, `famafrench`, `fred`, `oecd` —
  `DataReader(..., "stooq")` raises `NotImplementedError`. And the CSV endpoint
  it used to call now answers with a JavaScript **proof-of-work
  browser-verification challenge**. Stooq is dropped from the project; an
  adapter whose job includes defeating that is not something to ship. **FRED is
  the independent cross-check instead** — no API key, a genuinely separate
  pipeline, and it agreed with yfinance to the cent on `SP500`/`^GSPC`.
- **`yfinance` is 1.5.2, not 0.2.x.** `auto_adjust=True` is the default and it
  *removes* `Adj Close`; an unknown ticker returns an empty `(0, 6)` frame and
  logs rather than raising; columns are a `MultiIndex` even for one ticker; and
  **`end` is exclusive**, so passing the requested end through loses the last
  trading day of every window.
- **The adjustment policy is the thing that moves numbers**, not the vendor.
  AAPL on 2020-08-25 closes at `124.82` split-adjusted and `121.08`
  dividend-adjusted — 3.1% apart, same day, same source, and nothing in a
  `ResultSet` distinguishes them. So a `PriceSource.label` names its policy,
  and `DataQualityReport.source` carries it.
- **Ken French values are percent** (`Mkt-RF` of `-0.70` means −0.70%) and its
  index is `period[D]`. Forgetting the conversion rescales every loading by
  100. FRED hands back a `datetime64[us]` index and one column named for the
  series id.

### Two gaps found reading the tree, both open

- ~~**`DatasetSpec.risk_free` is dead.**~~ Closed by Task 6.3. It now resolves
  through a rate source into a `risk_free` column, and a spec that asks for one
  with no rate source configured is **refused** rather than analysed without it
  — running on raw instead of excess returns answers a different question and
  nothing downstream could tell.
- ~~**`ff3`, `ff5` and `carhart4` can never run.**~~ Closed by Task 6.4. Both
  gaps found reading the tree are now shut; `tests/data/test_live_integration.py`
  is where the whole chain is checked against real services, and it is the file
  to extend when a new source lands.

### Carried-over debts

- ~~**The grounding gate's false positives.**~~ Closed by Task 6.5, and both of
  them: the `(s3)` citation and the `(2020–2024)` year range. Adding a new
  exemption means keying it to something the plan actually declares, and
  proving it bites by over-widening it and watching a test fail.
- **PDF export.** Decided 2026-07-27: a **print stylesheet**, no new dependency
  in either stack. kaleido is ruled out rather than deferred — the backend holds
  no Plotly JSON, so it would mean reimplementing all fourteen TypeScript
  renderers in Python to export a chart nobody looked at. Task 6.14.
- **The Phase 4 e2e gate is model-dependent, not reliably green.**
  `analysis.spec.ts:227` asserts that an unpublished narration always carries
  grounding issues — but when the Validator refuses there is nothing to
  narrate and no issues to report. It fails on those runs and passes on
  others, and it was failing before Task 5.3 too (verified by stashing). The
  third path is simply uncovered. Task 6.16 fixes it.

### The synthetic source is permanent, not a placeholder

**`ECONOMETRICA_PRICE_SOURCE=synthetic`** makes the whole pipeline runnable with
no network at all, and it is genuinely reproducible — the seed is a hash of the
ticker, so re-running a manifest gets the same series back. Any run using it
carries a `synthetic_data` risk flag, which the canvas shows as an alert no tab
can hide. The real adapters are an **addition**; this one must keep working and
must keep carrying that flag.

That flag fires on a substring match — `"synthetic" in source.lower()` in
`DataSteward.resolve` — so **no real adapter's label may contain the word**, or
it would tell every reader its market data was generated. There is a test per
source.

Phase 4 is the interesting one: six agent roles, the deterministic
`DiagnosticsEngine` (already built, `econ/diagnostics/`) feeding a Validator on
a *different provider*, and the numeric grounding gate that blocks any number
in narrator prose that is not in `ResultSet.all_numeric_values()`.

### Runs and the canvas

- **The chat pane and the canvas are different things, and users conflate
  them.** A chat message streams tokens from a model and calls no tools, so it
  can never produce a chart; runs start from the canvas composer. The chat's
  empty state used to say "ask for an analysis and the results build up in the
  canvas", which is what a first user did, getting prose and no charts. If the
  two are ever unified, unify them deliberately — `runs.py` explains why the
  endpoints are separate.
- **Diagnostics have no chart type and are rendered directly.** A pure
  hypothesis test's finding is a statistic and a p-value in `diagnostics`,
  which binds to nothing in the chart union, so `propose_charts` returns
  nothing for `adf` and the canvas's Diagnostics tab shows it instead. The
  fallback used to emit stat tiles from whatever scalars existed, which for
  `adf` meant a canvas led by "Nobs: 5,080.0000" — see `_BOOKKEEPING`.
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
- **`getByLabel` and `getByRole` match on substring.** The canvas's "Analysis
  model" picker silently broke `chat.spec.ts`'s `getByLabel("Model")`, and a
  chat named `canvas` inside a project named `E2E canvas …` matched both. Short
  names in e2e locators need `{ exact: true }`.
- **Exports are built from the stored outcome, never by replaying a run.**
  `services/exports.py` owns the five data formats and puts the manifest in
  each; chart images come from the live Plotly graph in the browser, because
  the renderers are TypeScript and a server render would export a picture
  nobody looked at.

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
- **`pkill -f` does not kill a background process started from the Bash tool.**
  A uvicorn started for a live check survives it, the next one fails to bind
  with `[Errno 10048]`, and every request then hits the *old* code — which
  looks exactly like a fix not working. Stop it by port from PowerShell
  (`Get-NetTCPConnection -LocalPort … | Stop-Process`) and read the server log
  before believing a live result.

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
> `docs/plans/2026-07-24-econometrica-implementation.md`, then start Phase 6 —
> write its step-level plan first, as each earlier phase got one.

Task lists do **not** survive across sessions — this file and the plan document
are the memory. Update the "Where things stand" table when a phase moves.

**Keep `README.md` current in the same breath.** It is the one file a visitor
reads first, and it drifts silently because nothing tests it. When a task
lands, check its Status block, its repository layout, and any instruction the
change makes untrue — the "open the app at `localhost:5173`" line was wrong for
months because `npm run dev` binds `::1` only, and no test could have caught it.
