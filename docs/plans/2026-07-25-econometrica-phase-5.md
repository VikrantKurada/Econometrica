# Phase 5: Charts and the artifact canvas — step-level plan

> **For Claude:** the parent plan is
> `docs/plans/2026-07-24-econometrica-implementation.md`; the design rationale
> is `docs/plans/2026-07-24-econometrica-design.md` §7. **Load the `dataviz`
> skill before writing any chart code** — this document was written with it
> loaded, and the decisions below come from it.

**Goal:** results stop being JSON. A run produces charts a person can read and
interrogate, held in a canvas beside the conversation, exportable with the
manifest that reproduces them.

---

## Progress

| Task | State |
|---|---|
| 5.0 `acf` tool (prerequisite) | ✅ |
| 5.1 chart spec union | ✅ |
| 5.2 visualizer agent | ✅ |
| 5.3 chart renderers | ✅ |
| 5.4 artifact canvas | ✅ |
| 5.5 exports | ✅ |
| 5.6 phase 5 e2e | ✅ |

---

## What Phase 5 is allowed to assume

Verified in the tree at `d157c15`. **Every chart type below is backed by
something a tool already emits** — checked by running the tools, not by
reading their docstrings:

| Tool | Emits | Makes possible |
|---|---|---|
| `rolling_beta` | `beta`, `beta_ci_low`, `beta_ci_high` | rolling beta with confidence ribbon |
| `garch` / `egarch` / `gjr_garch` | `conditional_volatility`, `standardized_residuals` | conditional volatility panel, QQ, residual grid |
| `variance_ratio` | `vr_by_horizon` + `variance_ratios` table | variance ratio profile |
| `markov_switching` | `regime_1_prob`, `regime_2_prob` + `transition_matrix` | regime probability ribbon |
| `drawdown` | `drawdown` + `max_drawdown` table | underwater drawdown |
| `realized_vol` / `ewma_vol` | `realized_vol`, `ewma_vol` | volatility series |
| `irf` / `fevd` | `irf`, `fevd` tables | IRF grid, FEVD area |
| `engle_granger` | `spread` | cointegration spread |
| `hurst` | `rs_by_window` | rescaled-range log-log |
| `capm` / `ff3` / `ff5` / `carhart4` | `residuals`, estimates with CIs | SML scatter, coefficient forest, residual grids |
| every tool | `estimates`, `diagnostics`, `scalars` | forest plots, stat tiles, diagnostic tables |

Also available: `ResultSet.manifest` (for export provenance), `RunOutcome`
with its full trace, and `GET /api/runs/{id}`.

**Resolved before 5.1:** ACF/PACF values were emitted by no tool —
`ljung_box` returns a table of statistics, not autocorrelations — so the
design's stem charts had nothing to plot. An `acf` tool now exists
(`econ/efficiency/randomness.py`, 37 tools in the registry), emitting `acf`,
`pacf` and symmetric Bartlett bands as `*_upper`/`*_lower` series shaped for a
pair of stem panels. Computing them in the frontend was the one option ruled
out: it would put statistics above the tool boundary and break the project's
single invariant.

---

## Four decisions this plan settles

### 1. No dual-axis chart, so "conditional volatility overlay" becomes two panels

The design (§7) asks for a "conditional volatility overlay" — conventionally
price and fitted volatility on one plot with two y-scales. **The `dataviz`
skill names dual-axis the single most common chart mistake**, and it is: the
crossing point of two arbitrarily scaled series is an artifact of the scaling,
and readers infer causation from it.

**Decision.** `ChartSpec` has **no** second-axis field, so the mistake is
unrepresentable rather than discouraged. Price-and-volatility renders as two
stacked panels sharing one x-axis and one hover crosshair. Same for any
"overlay" of different units.

### 2. The series palette is adopted from `dataviz`, validated against *our* surfaces

The project's tokens carry `accent`, `positive` and `negative` but no
categorical series ramp. The skill's reference palette is adopted wholesale
and **re-validated against this project's own chart surfaces**, because
contrast results only mean anything against the surface actually rendered on.
Ours are `#fafafa` (light, `--surface-1`) and `#121416` (dark). Result:

```
light  ALL CHECKS PASS   worst adjacent CVD ΔE 9.1 · normal-vision 19.6
       WARN contrast: aqua 2.7, yellow 2.07, magenta 2.58 below 3:1
dark   ALL CHECKS PASS   worst adjacent CVD ΔE 8.4 · normal-vision 19.3
       contrast: all 8 ≥ 3:1
```

Three consequences that are requirements, not preferences:

- The light-mode WARN triggers the **relief rule**: those series ship visible
  direct labels or the table view. It is not dismissable.
- **Scatter, bubble and small-multiple forms cap at three series** — under
  all-pairs comparison only the first three slots clear the floors. Past
  three, facet or fold to "Other". This binds the SML scatter and the IRF
  grid.
- `accent`, `positive` and `negative` stay UI colors. A series must never be
  painted `accent`, or series identity and selection state become the same
  signal. Note `negative` (`#d33a3c`) sits near series-8 red and `positive`
  (`#269e5f`) near series-3 aqua: where a delta cue appears beside a series,
  the icon-and-label pairing carries it, never hue.

Add them to `frontend/src/styles/index.css` as `--series-1…8` under both the
`prefers-color-scheme` media query and the `[data-theme]` scope, as the
existing tokens already do — dark is a *selected* set of steps, never a flip.

### 3. Correlation heatmaps are diverging, not sequential

A correlation runs −1…+1 through a meaningful zero, so it takes the diverging
pair (blue↔red, **gray** midpoint), equal steps per arm. A cointegration
p-value heatmap runs 0…1 with no meaningful middle, so it takes the sequential
blue ramp. Getting this backwards is the classic error: a rainbow or a
single-hue ramp on correlation hides the sign.

### 4. Charts are specs, never code

The Visualizer emits `ChartSpec` JSON from a closed vocabulary and never
writes JavaScript — the design says so, and it is the same containment as the
tool registry. The renderer is a `switch` over spec types in the frontend. An
LLM-authored spec that does not validate is rejected with the same
retry-with-the-error loop `agents/base.py` already implements.

---

## Task 5.1: The chart spec union

**Files:** `backend/src/econometrica/charts/__init__.py`, `charts/spec.py`;
test `backend/tests/charts/test_spec.py`

A discriminated union over the chart types, `Annotated[..., Field(discriminator="type")]`,
so Pydantic rejects an unknown type with a message naming the valid ones.

**Types to cover**, each grounded in the table above: `line`, `line_with_band`,
`stacked_panels`, `scatter_fit`, `bar`, `forest`, `heatmap`, `qq`,
`residual_grid`, `stem`, `area_stack`, `step`, `histogram`, `underwater`,
`ribbon`, `small_multiples`, `stat_tile`, `table`.

**Tests must cover:**

- A valid spec of each type round-trips through `model_validate_json`.
- An unknown `type` is rejected and the error names the known ones.
- A spec referencing a series the `ResultSet` does not carry is rejected —
  a chart of data that does not exist is the visual equivalent of an
  ungrounded number.
- **No spec can express a second y-axis** (decision 1): a test asserts no
  model in the union has such a field, so the constraint survives someone
  adding a type later.
- A scatter/small-multiple spec with more than three series is rejected
  (decision 2), naming the cap and the reason.
- Series count beyond eight is rejected for every type.
- A `heatmap` declares its scale as `sequential` or `diverging` and a
  correlation-domain heatmap (−1…1) must be diverging (decision 3).

**Commit:** `feat(charts): add discriminated chart spec union`

**Landed with 14 types**, not the 18 drafted above: `small_multiples`,
`ribbon`, `step` and `residual_grid` all reduce to `panels` or `band`, and a
type nobody can distinguish from another is a type a Visualizer will pick
wrongly. The design's "~22" counts variants (ACF and PACF are two readings of
one `stem`, the IRF grid is one `panels`).

`unresolved_references()` returns problems rather than raising, because the
caller retries the model with them and needs all of them at once — the same
shape as `GroundingReport.summary()`.

**One dead-code catch worth repeating.** The scatter cap was declared as
`max_length=3` *and* as a validator with an explanatory message. Pydantic's
constraint fires first, so the explanation never ran, and the test passed only
because Pydantic's "at most 3 items" happens to contain a 3. Tightening the
regex to `three` exposed it. Where a message has to tell a model what to do
instead, the field constraint has to give way to the validator.

---

## Task 5.2: Visualizer

**Files:** `agents/visualizer.py`; test `tests/agents/test_visualizer.py`

Selects chart types from **result shape**, not from the question. The parent
plan's named tests: a GARCH result proposes a conditional volatility panel; a
VAR result proposes an IRF grid.

Worth noting: most of this is deterministic — the shape of a `ResultSet`
implies its charts, and a lookup from tool name to spec builders needs no
model. Follow the Phase 4 precedent: **make the mapping deterministic**, and
give the model only the genuinely editorial choices (which of several
candidates to show first, what to title them). A test should prove a chart is
produced with no provider at all.

**Commit:** `feat(agents): add visualizer over result shape`

**Landed split in two.** `charts/propose.py` is deterministic and decides what
a result supports; `agents/visualizer.py` is the model, and it may only
reorder, drop and retitle charts that already bind — it cannot invent one,
change a type, or change what data is drawn. So the role stays
model-assignable as the design intends, while the path most runs should take
(`propose_charts` with no provider) needs no turn at all.

**Rules key on shape before name**, which is what stops 37 tools needing 37
entries: any result carrying a `residuals` series gets a QQ plot, and a test
proves that using a `ResultSet` from an invented future tool.

**The invariant, parametrised over ten tools:** every proposed chart binds —
`unresolved_references` comes back empty against the result it was proposed
for.

Both named criteria hold against real tool output: GARCH gives a two-panel
chart sharing an x-axis (never two y-scales), and a two-variable IRF gives a
four-panel grid, one per `impulse → response` pair, sized from the series the
tool emits rather than from a hard-coded system size.

---

## Task 5.3: Chart renderers

**Files:** `frontend/src/components/charts/*`; tests alongside

One component per spec type, one shared Plotly layout module holding the
theme, and a partial Plotly bundle (the full one is ~3 MB).

**Non-negotiables from the skill**, each of which needs a test:

- Legend present whenever a chart has ≥ 2 series; absent for one (the title
  names it). ≤ 4 series are also direct-labeled, so identity is never
  color-alone.
- Every chart has a table view. It is the relief for the light-mode contrast
  WARN and the accessibility fallback.
- Crosshair-and-tooltip on line/area; per-mark tooltip on bar/dot/cell.
- Colors read from CSS custom properties, so the theme toggle repaints
  without a re-render path of its own.
- **Color follows the entity, not its rank** — hiding a series must not
  repaint the survivors. This is a real bug risk with Plotly's default
  colorway; assign explicitly per series id.

Then step 7 of the skill's procedure: **render it and look at it.** The
validator checks color, not layout. Screenshot each chart type in both modes
and check for label collisions and overflow before calling it done.

**Commit:** `feat(frontend): add themed chart renderers`

**Landed.** The palette was re-validated against `#fafafa` / `#121416` before
anything was written, and all four runs reproduce decision 2's numbers exactly
(adjacent 9.1/19.6 light, 8.4/19.3 dark; all-pairs over the first three slots
9.2/24.0 light, 9.4/20.9 dark). Those are `--surface-1`, so **the chart card is
`bg-surface-1`** — on `surface-0` the recorded contrast would be a number about
a surface the chart no longer sits on.

Three things worth carrying forward:

- **Looking at it caught four defects tests did not.** Two end labels
  overlapping where the volatility series converge; bar values printed
  `0.970` beside `1.04`; Plotly's modebar landing on the legend as a dark
  slab; and the table view running to 260 rows and setting the card's height.
  Each is now fixed, and the label collision has a rule and a test —
  converging labels are dropped, because nudging them apart detaches them
  from their lines. The legend and the table view carry identity instead.
- **The renderer never fits anything.** A `scatter` with `fit: true` draws the
  line from the result's own intercept and slope estimates, and draws no line
  at all when the result carries none. A regression run in the browser would
  be a statistic above the tool boundary with no manifest behind it. Sorting
  for a QQ plot and binning a histogram are display transforms and stay.
- **`reference: "t"`** on a QQ spec cannot be drawn — nothing on the wire
  carries the degrees of freedom — so it renders the "could not be drawn"
  notice rather than normal quantiles under a t label.

The partial bundle is `plotly.js/lib/core` plus four traces (scatter, bar,
heatmap, histogram), which is every one of the fourteen types; `stat_tile` and
`table` are HTML and touch none of it. Plotly's CommonJS reaches for Node's
`global`, so `vite.config.ts` defines it as `globalThis` — without it the
charts throw on first import.

`/gallery.html` is a dev-only harness rendering one card per type over
fixtures. `vite build` only takes `index.html`, so it never ships. It is the
fastest way to look at 5.4 and 5.5 too.

---

## Task 5.4: Artifact canvas

**Files:** `frontend/src/components/canvas/*`

Tabbed canvas with pinning, full-screen and re-run. **This is also where the
run UI lands** — `POST /api/chats/{id}/runs` and `GET /api/runs/{id}` have no
frontend at all, which is why the Phase 4 gate is API-level.

Two things this must surface, both already computed and currently invisible:

- **The `synthetic_data` risk flag.** A canvas that renders generated prices
  as though they were market data undoes the honesty the Data Steward was
  built for.
- **Refusals and unjudged checks.** A refused GARCH step is a finding. The
  canvas showing only what ran would misrepresent the analysis.

Re-run is the plan's definition-of-done item: re-running a manifest must
reproduce the result.

**Landed, and it needed more than `components/canvas/*`.** The file list above
was wrong: none of what the canvas must show was reachable from any API.
Three things had to exist first.

- **Runs never produced charts.** The Visualizer was built in 5.2 and nothing
  called it, so `RunOutcome` had no charts at all. `propose_charts` now runs
  over each step's `ResultSet` in the orchestrator and stamps `step_id` on
  what it returns, which is what lets a chart be traced back and re-run. It
  stays deterministic: the `Visualizer` curates *one result per turn*, so
  wiring it in unconditionally would put a model call behind every result a
  run produced. That is a cost the canvas should choose, not the pipeline.
- **The outcome was never persisted.** `record_run` wrote the run row and the
  step DAG, so a run was only readable while its SSE stream was open. `runs`
  now carries an `outcome` JSONB column (migration `61162a63a8b7`) and
  `RunDetail` returns it. `RunRead` deliberately does not: a result's series
  live in there, so listing runs would drag every one of them along.
- **Re-run had nothing to re-run from.** `POST /api/runs/{id}/rerun`
  re-executes the recorded plan against freshly resolved data and compares
  manifests *and* numbers. It asks no model anything — re-planning would test
  whether a model repeats itself, which the manifest promises nothing about —
  and a test asserts the model call count is unchanged.

**Re-run found a real bug in Phase 4 on its first live use.** The dataset was
resolved once, before the revision loop, so a revised plan executed against
the *previous* plan's frame. The run recorded that first analysis as
`plan.dataset: 2020-01-01..2023-12-31` while the numbers came from
`2000-01-01..2023-12-31` — the plan was a wrong account of its own results,
and re-running it disagreed for a reason that had nothing to do with the data
source. The orchestrator now re-resolves when a revision changes the spec, and
does not re-fetch when it does not. **With that fixed, a live run through the
UI re-runs and reports `reproduced`**, which closes the last open item in the
parent plan's definition of done.

Two smaller things the plan did not anticipate:

- **A run needs models assigned before it will start**, and nothing exposed
  `Project.model_assignments`. The composer writes them just before starting.
  The Validator gets its own picker, defaulting to the analysis model — when
  they match, the pipeline's independence warning fires and the canvas shows
  it, which is the honest outcome rather than a hidden one.
- **`getByLabel` matches on substring**, so the canvas's "Analysis model"
  picker broke `chat.spec.ts`'s `getByLabel("Model")`. Fixed with
  `{ exact: true }` — the same hazard its own `Message` locator documents.

What the canvas refuses to tab away: the risk flags and the findings. They
qualify every artifact at once, so a reader who never opens the right tab
still sees that the prices were generated and that a step refused.

**Commit:** `feat(frontend): add tabbed artifact canvas`

---

## Task 5.5: Exports

**Files:** `api/routers/exports.py`

PNG, SVG, PDF, CSV, XLSX, JSON, Markdown, and a project ZIP carrying the
reproducibility manifest. Every export embeds the manifest or ships beside it
— an exported chart that cannot be traced back is exactly what this project
exists not to produce.

**Commit:** `feat(api): add artifact exports with manifests`

**Landed, split by where the artifact lives.** Five formats behind one route,
`GET /api/runs/{id}/export?format=…`, all built from the stored outcome — so
an export replays no analysis and asks no model anything. JSON nests the
manifest, Markdown gives it a Provenance section, XLSX puts it on its own
sheet, the ZIP ships it as `manifest.json`, and CSV — which has no metadata
channel — carries it in comment lines that `pandas.read_csv(comment="#")`
skips and a person can read.

**Chart images are exported by the frontend, not the API.** The fourteen
renderers are TypeScript; a server-side PNG would mean reimplementing Task 5.3
in Python, and would export a chart nobody had looked at. `Plotly.toImage` on
the live graph carries the reader's theme and any zoom they set.

**PDF is the one thing not built.** Neither a chart nor a report can become
one without a new dependency — kaleido for charts, or a HTML-to-PDF engine for
reports — and the browser's own print pipeline covers the report case for free
in the meantime. It needs a dependency decision rather than a quiet omission.

The real output taught two things the tests had not: a refused step has no
manifest and rendered as blank cells that read like lost provenance rather
than a step that produced nothing; and the exported report made the grounding
gate legible for the first time — it had rejected `-15.066` where the computed
statistic is `-15.065457`, so the model fabricated a digit and the gate caught
it.

---

## Task 5.6: Phase 5 e2e

Produce a chart, interact with it, export it, confirm the file opens. Unlike
Phase 4's gate this one **can** be browser-level, because 5.4 gives runs a UI.
Follow `e2e/analysis.spec.ts` for the model-selection and skip-with-a-reason
pattern, and `ECONOMETRICA_PRICE_SOURCE=synthetic` for data.

**Commit:** `test(e2e): close phase 5 with a chart and an export`

**Landed as `e2e/canvas.spec.ts`**, driving the app the way a person does:
type the question, pick the model, watch the phases, read the chart, switch it
to a table, open it full screen, check diagnostics and trace, download the
archive, open it. ~35s against `ministral-3:8b`.

Two things it is careful about. The **synthetic_data alert is asserted**, so
the gate proves the honesty seam and not only the pipeline. And it **does not
assume a chart exists**: which tools a model plans is its choice, and a plan of
only hypothesis tests legitimately draws nothing — so the spec asserts a chart
*or* the "produced no charts" message, and annotates which happened. Asserting
a chart unconditionally would make the gate a test of the model's taste.

The archive is opened with `adm-zip` and its manifest checked for the data
fingerprint, the tool versions and the `synthetic_data` flag — the claim the
project rests on, verified on the file a user actually walks away with.

---

## Phase 5 definition of done

- `uv run pytest`, `npx vitest run`, `npx tsc --noEmit`, `npm run test:e2e` green;
  `ruff` and `mypy src` clean.
- Every chart type renders in both themes, screenshotted and looked at.
- The palette validator passes against both project surfaces (re-run it if any
  surface token changes).
- No chart in the codebase has two y-scales.
- **Re-run reproduces a result from its manifest** — the last open item in the
  parent plan's definition of done.
