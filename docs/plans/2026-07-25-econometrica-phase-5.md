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
| 5.1 chart spec union | ⬜ next |
| 5.2 visualizer agent | ⬜ |
| 5.3 chart renderers | ⬜ |
| 5.4 artifact canvas | ⬜ |
| 5.5 exports | ⬜ |
| 5.6 phase 5 e2e | ⬜ |

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

**Commit:** `feat(frontend): add tabbed artifact canvas`

---

## Task 5.5: Exports

**Files:** `api/routers/exports.py`

PNG, SVG, PDF, CSV, XLSX, JSON, Markdown, and a project ZIP carrying the
reproducibility manifest. Every export embeds the manifest or ships beside it
— an exported chart that cannot be traced back is exactly what this project
exists not to produce.

**Commit:** `feat(api): add artifact exports with manifests`

---

## Task 5.6: Phase 5 e2e

Produce a chart, interact with it, export it, confirm the file opens. Unlike
Phase 4's gate this one **can** be browser-level, because 5.4 gives runs a UI.
Follow `e2e/analysis.spec.ts` for the model-selection and skip-with-a-reason
pattern, and `ECONOMETRICA_PRICE_SOURCE=synthetic` for data.

**Commit:** `test(e2e): close phase 5 with a chart and an export`

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
