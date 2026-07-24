# Econometrica — Design

**Date:** 2026-07-24
**Status:** Approved

A GenAI-powered econometrics workbench for financial asset pricing and market
efficiency analysis. The user drives analysis through chat; the application
answers with interactive charts and artifacts backed by validated,
reproducible econometric computation.

---

## 1. Product shape

A local three-pane web application. Python analysis backend, React frontend,
Postgres. Started with `docker compose up`, used in the browser.

```
┌────────────┬─────────────────────────────┬──────────────┐
│ Projects   │   Artifact Canvas           │  Chat        │
│  └ Chats   │   (charts, tables, traces)  │  (stream)    │
│            │   tabbed · pinnable         │              │
│ 260px      │   flexible                  │  420px       │
└────────────┴─────────────────────────────┴──────────────┘
```

All three panes are resizable and collapsible. The visual language is
minimalist and professional: a neutral gray scale, a single accent colour,
Inter, dense but breathable spacing, light and dark themes. Charts carry the
visual weight; application chrome recedes.

Single user, no authentication. This keeps the local Ollama instance reachable
and removes an entire tier of scope.

---

## 2. The central decision: how GenAI produces econometrics

Three options were considered.

**A — Tool registry only.** The LLM never writes code; it selects from a set of
typed, versioned econometric functions. Deterministic and fully traceable, but
bounded by the registry.

**B — Code generation in a sandbox.** The LLM writes statsmodels/arch code that
is executed. Unbounded, but brings hallucinated methodology, non-reproducible
results, a real security surface, and nothing that can be unit-tested ahead of
time. Wrong default for a tool whose value is trustworthy numbers.

**C — Registry-first with a gated code escape hatch. (chosen)** The registry
serves the canonical majority of requests. When the planner finds no fitting
tool, a Quant Coder agent writes code executed in a locked-down subprocess — no
network, no filesystem, whitelisted imports, CPU/memory/wall-clock caps — and
the Validator must sign off before results surface. The code path is **off by
default**, enabled per project, and its outputs are marked in the UI as using
an unvalidated method.

C is the only option satisfying both "highly versatile" and "numbers worth
staking a decision on." It is also the most security-sensitive component, so it
is built last.

---

## 3. Technology stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | Python **3.12** (pinned via uv), FastAPI, Pydantic v2 | Python 3.14 is installed locally, but `arch`, `numba` and `linearmodels` wheels lag behind it; 3.12 avoids building from source |
| Econometrics | statsmodels, arch, linearmodels, scipy, ruptures, pandas, numpy | Reference implementations; nothing reinvented |
| Database | Postgres 16 + TimescaleDB + pgvector | Hypertables for price/return series, JSONB for artifacts/messages/traces, vectors for document retrieval — one engine, three jobs |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | |
| Job execution | asyncio + `ProcessPoolExecutor` + a `jobs` table, progress streamed over SSE | GARCH and VECM fits are CPU-bound and would block the event loop. Redis/Celery is unjustified complexity for a single-user local app |
| Frontend | React 19 + TypeScript + Vite, TanStack Query, Zustand, Tailwind + Radix primitives | |
| Charts | Plotly.js, custom partial bundle, with a theme layer | The only library carrying the full statistical vocabulary natively — QQ plots, ACF stems, heatmaps, error bars, subplot grids, candlesticks, WebGL for long series |
| Market data | yfinance, Stooq, FRED, Ken French data library | Free, no API keys required to start |
| Testing | pytest + hypothesis (core), Vitest + Playwright (UI) | |

Known trade-off: the trimmed Plotly bundle is roughly 1 MB gzipped. Acceptable
for a locally served application.

---

## 4. Agent architecture

Six roles, each independently assignable to a provider and model per project.

1. **Planner** — turns user intent, project context and dataset schema into a
   typed `AnalysisPlan`: assets, window, frequency, methods, hypotheses, chart
   intents.
2. **Data Steward** — resolves tickers and uploads, aligns trading calendars,
   handles frequency conversion, missing data, currency, and log-versus-simple
   returns. Emits a `Dataset` handle and a data-quality report.
3. **Econometrician** — binds plan steps to registry tools with validated
   parameters. Enforces method preconditions: stationarity before VAR, ARCH-LM
   before GARCH, lag selection before Johansen.
4. **Validator** — deliberately assigned to a *different provider* than the
   Econometrician. Reviews the plan, parameters, deterministic diagnostics and
   conclusions. May reject and return for revision, bounded to N cycles.
5. **Visualizer** — emits declarative `ChartSpec` JSON drawn from a closed
   vocabulary. Never writes JavaScript.
6. **Narrator** — writes the interpretation, constrained to cite artifact and
   statistic identifiers.

### Validation is not left to LLM judgement

A deterministic `DiagnosticsEngine` runs *before* the Validator and supplies it
with facts rather than asking it to infer them: Jarque-Bera, Breusch-Pagan and
White, Durbin-Watson, Ljung-Box on residuals, VIF, CUSUM and Chow structural
break tests, sample adequacy, and effective observation counts.

A **numeric grounding gate** then extracts every number appearing in the
Narrator's prose and matches it against the result JSON. Unmatched numbers
block the message. This is a mechanical, testable anti-hallucination check and
is the single most important safeguard in the system.

### Validation tiers

Set per project:

- `single` — no critic; fastest and cheapest.
- `critic` — Validator reviews every run. **Default.**
- `consensus` — Econometrician planning runs on N providers; plans and
  interpretations are diffed, and disagreement is surfaced to the user rather
  than silently resolved.

The deterministic gates (diagnostics, numeric grounding, schema validation) run
in all tiers.

---

## 5. Provider layer

A single `LLMProvider` interface with capability flags for tool calling, JSON
mode, streaming and context window. Adapters for **Ollama** (local), **NVIDIA
NIM**, **Gemini**, **Claude** and **OpenAI**.

API keys live in an encrypted local store, never in the database as plaintext.

Per-role model assignment is a first-class feature: a frontier model for the
Planner, a different vendor for the Validator to obtain genuine independence,
and local Ollama for routine classification at zero cost.

---

## 6. Econometric coverage

All four families ship in the first release.

**Asset pricing.** CAPM; Fama-French 3- and 5-factor; Carhart 4-factor;
alpha and beta with Newey-West and White robust standard errors; rolling betas
with confidence bands; Fama-MacBeth cross-sectional regressions; GRS test;
security market line.

**Market efficiency.** ADF, KPSS and Phillips-Perron unit root tests;
Lo-MacKinlay variance ratio with heteroskedasticity-robust statistics; runs
test; Ljung-Box; BDS independence; Hurst exponent via rescaled range; a
composite weak-form efficiency score.

**Volatility and risk.** GARCH, EGARCH and GJR-GARCH with normal, Student-t and
skew-t innovations; EWMA; realized volatility; historical and parametric
VaR/CVaR with Kupiec and Christoffersen backtests; drawdown analytics.

**Multivariate and event study.** VAR and VECM; Johansen and Engle-Granger
cointegration; Granger causality; impulse response functions with bootstrap
confidence intervals; forecast error variance decomposition; Markov regime
switching; event-study CAR/CAAR with significance tests.

Every tool is developed test-first against known-answer fixtures and
property-based invariants — the beta of an asset regressed on itself is 1,
VR(1) is 1, simulated-GARCH parameter recovery falls within the confidence
interval, Johansen rank is recovered on synthetic cointegrated systems.

---

## 7. Charts

Roughly 22 declarative spec types, selected by the Visualizer to match result
shape: rolling beta with confidence ribbon; security market line scatter with
fitted line; conditional volatility overlay; QQ and 2x2 residual diagnostic
grids; ACF and PACF stems with Bartlett bands; impulse response subplot grid
with bootstrap bands; coefficient forest plot; correlation and cointegration
heatmaps; event-study CAAR with confidence interval; regime probability ribbon;
variance ratio profile across horizons; underwater drawdown; VaR exceedance
timeline.

All are interactive — zoom, pan, hover, series toggle, and crossfilter where
meaningful — and all are exportable.

---

## 8. Traceability and telemetry

Every assistant turn creates a **Run** holding a DAG of **Steps**. Each step
records the agent, provider, model, prompt and response, token counts, cost,
latency, tool calls with input and output hashes, and parent links. A **Trace**
artifact renders this DAG in the canvas, so any turn can be opened to see
exactly which model decided what.

Each result carries a **reproducibility manifest**: a SHA-256 of the exact
aligned input matrix, the tool name and version, parameters, library versions
and RNG seed. A **Re-run** action re-executes and diffs against the original —
either bit-identical, or it reports what moved.

Non-functional telemetry uses OpenTelemetry, with spans written to Postgres and
optional OTLP export. Tracked: latency p50/p95/p99, token spend by provider and
role, tool error rates, validator rejection rate, plan revision counts, job
queue depth, and database timings.

---

## 9. Files, web search, and MCP

Uploaded CSV, XLSX and Parquet files are profiled, their schema inferred, and
column roles (date, ticker, price, return, volume, factor) mapped with LLM
assistance that **the user confirms** before ingest. Data is stored long-format
in Timescale with the original blob retained. PDFs and text documents are
chunked into pgvector for retrieval context.

Web search and MCP are per-project settings that each chat inherits and can
override. Both are **off by default**. The application acts as an MCP client;
discovered tools pass through an explicit allowlist before any agent may call
them.

---

## 10. Downloads

- Chart — PNG, SVG, PDF
- Result tables — CSV, XLSX, JSON
- Chat — Markdown, PDF
- Project — ZIP containing all artifacts, datasets, traces and the
  reproducibility manifest

---

## 11. Build order

Each phase ends with something runnable.

1. **Skeleton** — database, migrations, three-pane shell, project and chat CRUD.
2. **Econometrics core** — all four families, test-first, no LLM involved.
3. **Provider layer** — the five adapters, plus single-agent chat.
4. **Multi-agent orchestration** — the six roles, diagnostics engine, numeric
   grounding gate, validation tiers.
5. **Charts and artifact canvas** — ChartSpec vocabulary, Plotly renderer,
   interactivity, exports.
6. **Platform** — telemetry, file uploads, web search, MCP client, project
   export.

The sandboxed code escape hatch is implemented last, behind its toggle, once
everything else is stable.
