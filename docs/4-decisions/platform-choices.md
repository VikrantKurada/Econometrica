# Platform choices

Ten decisions about the stack. Several of them look boring and are not, and
two of them cost a working day each to learn.

---

<a id="d12"></a>

## D12. Single user, no authentication

### Decision

No login, no accounts, no permissions. One person, one machine.

### Why

It removes an entire tier of the product: sessions, password handling,
authorization on every route, a user model threaded through every query, and
the tests for all of it.

It also keeps the local Ollama instance reachable without a proxy, which is
what makes the zero-configuration path work.

### What it costs

You cannot share a project with a colleague, and the concept of "who ran this"
does not exist. For a workbench whose whole point is auditable analysis, that
is a real gap.

### When it changes

It is the first thing the [medium-term roadmap](../5-roadmap/medium-term.md)
revisits, and the schema is already close: everything is scoped by project,
and a project would take an owner column rather than a redesign.

---

<a id="d13"></a>

## D13. Postgres with TimescaleDB and pgvector

### Decision

One database engine doing three jobs.

| Job | Mechanism |
|---|---|
| Time series at volume | A TimescaleDB hypertable on `observations` |
| Semi-structured artifacts | JSONB: `runs.outcome`, `model_assignments`, `mcp_servers`, span attributes |
| Semantic retrieval | pgvector on `document_chunks.embedding` |

### The alternative

Three stores: a time-series database, a document store, and a vector database.
Each better at its job, and three sets of operational concerns, three
consistency stories, and three things to install before the application runs
at all.

For a single-machine workbench, one engine wins on every axis that matters.

### Three things about the hypertable Alembic cannot see

Recorded here because each one costs an afternoon when rediscovered.

1. **The conversion is invisible to autogenerate.** `create_hypertable` is
   hand-written in the migration and asserted against Timescale's catalogue in
   a test.
2. **It creates its own index**, which made `alembic check` want to drop one
   on every single run. So `create_default_indexes => FALSE`, and we declare
   `ix_observations_ts` ourselves.
3. **`field` is part of the primary key.** A wide file mapping both a close and
   a volume has two rows per `(ts, symbol)`.

### And one about Alembic generally

Autogenerate **does** emit CHECK constraints when it creates a table. What it
cannot see is one added to or changed on a table that already exists: that
revision comes out empty and has to be hand-written.

`alembic check` verifies neither case, so tests are the only gate. And
asserting the constraint *names* is not enough, which
[R6 in the reversals page](reversals.md#r6) covers.

---

<a id="d14"></a>

## D14. Python pinned to 3.12

### Decision

```toml
requires-python = ">=3.12,<3.13"
```

### Why

The system has 3.14. `arch`, `numba` and `linearmodels` publish no 3.14
wheels, so 3.14 means building from source, which means a C toolchain, which
means the zero-configuration path is gone.

`uv` downloads 3.12 automatically, so there is no system Python 3.12 install
to arrange.

### Do not "fix" the pandas version either

pandas 3.0.5 is fine. All 21 econometric paths were probed against it. **Do
not pin back to 2.x.**

One sharp edge: pandas 3 **rejects the `M`, `Q` and `A` resample aliases
outright.** `resample("M")` raises `ValueError`, it does not warn. Meanwhile
`DatasetSpec.frequency` and `econ.returns.PERIODS_PER_YEAR` still speak those
letters, so the mapping to `ME`, `QE`, `YE` happens once, at the boundary in
`agents/data_steward.py`.

---

<a id="d15"></a>

## D15. Plotly, as a trimmed partial bundle

### Decision

Plotly.js with a custom partial bundle and a theme layer.

### Why Plotly and not something smaller

It is the only library carrying the full statistical vocabulary natively: QQ
plots, ACF stems, heatmaps, error bars, subplot grids, candlesticks, and WebGL
for long series.

Anything smaller means implementing several of those, and a hand-rolled QQ
plot is a place to be subtly wrong.

### The cost, stated

The trimmed bundle is roughly 1 MB gzipped. Acceptable for a locally served
application, and it would not be for a public web app.

The bundle is `lib/core` plus four traces, not the ~3 MB whole.

### The gotcha

**Plotly needs `global`.** Its CommonJS build reaches for the Node global, so
`vite.config.ts` defines it as `globalThis`. Without that, the charts throw on
first import, which looks like a bundler problem and is not.

---

<a id="d16"></a>

## D16. No chart type can express a second y-axis

### Decision

There is no field for a second y-axis **anywhere** in the chart spec union,
and a test asserts the absence across every member so a type added later
cannot reintroduce it.

### Why this is not a style rule

Two measures on two scales share a crossing point that is an **artifact of the
scalings**, and readers reliably infer causation from it. It is one of the few
chart forms that is actively misleading rather than merely suboptimal.

### What to do instead

Two measures become a `PanelsChart`: stacked panels sharing one x-axis and one
crosshair. Colour is assigned per panel, so the adjacent-pair colour rule
governs rather than the all-pairs cap.

### Two related caps, both measured

**Eight series** clear the adjacent-pair colour-blindness floors on this
project's own chart surfaces.

**Three series** on a scatter, because a scatter compares every pair at once
and only the first three palette slots clear the all-pairs floors.

These are measured numbers, not chosen ones. The chart card is `bg-surface-1`
(`#fafafa` light, `#121416` dark) because those are the surfaces the palette
was validated against. **Re-run the validator if either token moves.**

And `--series-1` through `--series-8` are hex while every neighbouring token
is oklch, because they are the exact steps the validator was run on and
converting them rounds the values the separations were measured from.

---

<a id="d17"></a>

## D17. Runs and chat messages are separate routes

### Decision

`POST /api/chats/{id}/messages` streams tokens.
`POST /api/chats/{id}/runs` streams pipeline phases.

Not one route with a mode flag.

### Why

They differ in every way that matters to a client:

| | Messages | Runs |
|---|---|---|
| Event vocabulary | tokens | phases |
| Failure model | a failed turn is an error | a refused run still returns results |
| Duration | seconds | minutes |
| Produces charts | never | yes |

Folding them together would make one route's response shape depend on a
request field, which neither the frontend's stream reader nor its tests could
narrow on.

### The product consequence

**Users conflate the chat pane and the canvas, and this is where it shows.** A
chat calls no tools, so it can never produce a chart. The chat's empty state
used to say "ask for an analysis and the results build up in the canvas",
which is exactly what the first user did, getting prose and no charts.

If the two are ever unified, unify them deliberately. `runs.py` explains why
they are separate at the top of the file, so nobody merges them by accident.

---

<a id="d18"></a>

## D18. asyncio and a process pool, not Redis or Celery

### Decision

`asyncio` plus a `ProcessPoolExecutor`, with progress streamed over SSE.

### Why

GARCH and VECM fits are CPU-bound and would block the event loop, so something
has to move them off it. But Redis and Celery are unjustified complexity for a
single-user local application: two more processes to install, run and debug,
to solve a queueing problem that does not exist at one user.

### The cost

There is no durable job queue. A long fit occupies a request for its duration,
and a restart mid-fit loses it.

### When it changes

When more than one person uses one instance, which is
[the medium-term roadmap's](../5-roadmap/medium-term.md) job queue item. The
design already anticipated a `jobs` table.

---

<a id="d19"></a>

## D19. Port 8001, not 8000

### Decision

`start.ps1` puts the API on 8001 and points the Vite proxy at it through
`ECONOMETRICA_API_URL`. The end to end suite uses 8100 and 8101.

### Why this is a decision and not a detail

Because it cost a day, and because the failure mode is indistinguishable from
"the fix did not work".

Another container on this machine holds the **wildcard** address on 8000. A
wildcard socket answers `127.0.0.1` traffic too, so naming `127.0.0.1`
explicitly does not save you.

Measured, 2026-07-30: with uvicorn bound to `127.0.0.1:8000` and
`Get-NetTCPConnection` naming it as the listener, **25 consecutive polls of
`http://127.0.0.1:8000/api/health` came back 404 with `Server: SurrealDB`**.
The same URL answered 200 from uvicorn minutes later.

It is a race, not a rule. That is precisely what makes it so expensive: an
intermittent wrong answer reads as your own bug.

### The related one

**`uvicorn --reload` cannot be stopped by port.** The reloader binds the socket
in the *parent* and hands it to the child, so killing either leaves an orphan
holding the port. The next start fails with `[Errno 10048]` and every request
reaches the old code.

`start.ps1` runs without `--reload` and records the window pids so `-Stop` can
kill the whole tree.

And `pkill -f` does not kill a background process started from a Bash tool. A
uvicorn started for a live check survives it, the next one fails to bind, and
every request then hits the old code, which again looks exactly like a fix not
working.

---

<a id="d20"></a>

## D20. PDF from a print stylesheet

### Decision

`styles/print.css`, driven by the browser's own print pipeline. No new
dependency in either stack.

### Why not a rendering library

kaleido was **ruled out rather than deferred**, and the reason is structural:
the backend holds no Plotly JSON. Server-side chart export would mean
reimplementing all fourteen TypeScript renderers in Python, in order to export
a picture nobody had looked at.

See [R2](reversals.md#r2).

### What the stylesheet does

- Forces light surfaces whatever theme the reader was using
- Drops the application chrome
- Keeps a chart card whole across a fold
- **Always prints the `Provenance` block**, which is print-only and always
  present, because a printed artifact that cannot be traced back is what this
  project exists not to produce

### The consequence nobody expects

Canvas tab panels are **force-mounted**, so paper gets all of them. And an
inactive panel is parked *off-screen* rather than hidden, because **a Plotly
chart in a `display: none` container renders blank** and would print empty.

Radix sets `hidden` on a panel it unmounts and not on a force-mounted one, and
the CSS keyed on `[hidden]`. So Narrative, Diagnostics and Trace all rendered,
stacked, under whichever chart was open. The rule keys on
`[data-state="inactive"]` now.

**Verifying print means applying the parsed rules to a live DOM.** `@media
print` never engages on screen. Read them from `document.styleSheets`, not by
fetching the `.css`: in dev, Vite serves it as a JS module, so the text comes
back escaped and every rule parses empty.

---

<a id="d21"></a>

## D21. Telemetry and the run trace are separate, and nothing is summed from both

### Decision

Two records that never mix.

| | `run_steps` | `spans` |
|---|---|---|
| Records | Every model call and tool invocation | HTTP handlers, database timings, transport |
| Carries | agent, provider, model, tokens, cost, latency, prompt, response, parent | name, kind, status, duration, attributes |
| Token or cost columns | yes | **none at all** |

`GET /api/metrics` reads latencies from spans and tokens from steps.

### Why the separation is structural

A cost that counted each model call twice would look **entirely plausible and
be entirely wrong**. That is the dangerous kind of bug: no exception, no
anomaly, just a number that is 2x.

So `spans` has no token or cost column, and a test asserts the columns do not
exist. The mistake is not prevented by discipline. It is prevented by there
being nowhere to put the data.

### Three properties of `span()`

- **Inert until configured.** Telemetry may never break what it measures.
- **Swallows sink failures.** Same reason.
- **The tracer provider is deliberately not registered globally.** That can
  only happen once per process, which would make a batch exporter impossible
  to shut down.

OTLP is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set, and its export
timeout is 2 seconds so an unreachable collector cannot hold a shutdown open.

### The `SpanWriter` owns its sessions

A span outlives the request that produced it, and writing it inside that
request's transaction would make **telemetry able to roll a user's work
back**.

### Rates with no denominator are `null`

Not `0.0`. Zero reads as "nothing ever failed", which is a claim. "Nothing has
run yet" is a different statement and the dashboard should be able to make it.

---

## The one about the operating system

Not a decision so much as a set of facts, collected because each one cost real
time.

| Fact | Consequence |
|---|---|
| Docker Desktop does not autostart here (`AutoStart: false`) | A fresh boot means no Postgres and ~40 test errors |
| Docker Model Runner is disabled (`"EnableInference": false`) | It was crash-looping Docker Desktop through an orphaned socket. Leave it off |
| A `.ps1` with no BOM is read as **ANSI** by Windows PowerShell 5.1 | An em dash in a double-quoted string decoded to `â€"`, whose embedded `"` closed the string and produced a `Missing closing '}'` parse error pointing at the wrong line. `start.ps1` is ASCII-only *and* BOM'd |
| PowerShell mangles `git commit -m` when the message has double quotes | It re-parses before handing off to git, silently turning part of the message into a pathspec. Write the message to a file and use `git commit -F` |
| `git` writes progress to stderr | PowerShell surfaces it as `NativeCommandError`. A push that prints `* [new branch]` succeeded |
| Vite binds only the first address the OS resolves | On Windows that is `::1`, so `127.0.0.1:5173` is refused outright. Open `localhost:5173`, or pass `--host 127.0.0.1` as `playwright.config.ts` does |

The Vite one is worth singling out: the README told people to open
`127.0.0.1:5173` for months and it was simply wrong, because nothing tests a
README.

---

[Documentation](../README.md) · [4. Decisions](README.md) ·
[The central decision](the-central-decision.md) ·
[Trust mechanisms](trust-mechanisms.md) · **Platform choices** ·
[Reversals](reversals.md)
