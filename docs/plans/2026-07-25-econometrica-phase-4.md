# Phase 4: Multi-agent orchestration — step-level plan

> **For Claude:** the parent plan is
> `docs/plans/2026-07-24-econometrica-implementation.md`; the design rationale
> is `docs/plans/2026-07-24-econometrica-design.md` §4. This document expands
> that plan's Phase 4 task table to step level, as the parent plan says each
> phase's plan should be when the phase is reached.

**Goal:** a user asks a question in prose and receives an answer whose every
number is traceable to a `ResultSet`, whose method choice was refused if it
violated a precondition, and whose reasoning was reviewed by a model from a
different vendor than the one that made the choice.

Phase 3 ended with one model answering directly. Phase 4 puts six roles, two
deterministic gates and a bounded revision loop between the question and the
answer.

## Progress

| Task | State |
|---|---|
| 4.1 agent schemas | ✅ |
| 4.2 planner | ✅ |
| 4.3 data steward | ✅ |
| 4.4 econometrician + gates | ✅ |
| 4.5 validator | ✅ |
| 4.6 numeric grounding gate | ✅ |
| 4.7 narrator | ✅ |
| 4.8 orchestrator | ✅ |
| 4.9 run and step persistence | ✅ |
| 4.10 phase 4 e2e | ⬜ next — see the note under 4.10 |

Task 4.1 turned up a fourth thing the tree did not provide: **the running
server's tool registry was empty.** Registration is an import side-effect of
the five family packages, and nothing under `api/` imported any of them —
invisible while no request path resolved a tool by name, fatal the moment
`PlanStep` validates one. `econ.load_tools()` now exists and `main.py` calls
it, proven by a subprocess test, because every test module under `tests/econ`
imports the family it exercises and so an in-process assertion would pass
regardless.

---

## What Phase 4 is allowed to assume

All verified in the tree as of `560d2c3`:

| Thing | Where | Shape |
|---|---|---|
| 36 typed tools | `econ/registry.py` | `RegisteredTool(name, version, family, summary, params_model, fn, preconditions)` |
| LLM tool schemas | `ToolRegistry.to_tool_schemas()` | `{name, description, input_schema}` — already the shape `llm.types.ToolSpec` takes |
| Results | `econ/types.py` | `ResultSet` with `.estimate(name)` and `.all_numeric_values()` |
| Diagnostics | `econ/diagnostics/engine.py` | `run_diagnostics(resid, exog) -> list[Diagnostic]`, nine checks, tri-state `passed` |
| Providers | `llm/base.py` | `LLMProvider` protocol: `complete`, `stream`, `list_models`, `health` |
| Provider registry | `llm/registry.py` | `spec(name)`, `is_configured(name)`, `build(name)` |
| A scriptable provider | `llm/fake.py` | `FakeProvider` records every call — the spy every agent test uses |
| Role→model mapping | `Project.model_assignments` | JSONB, `{"planner": {"provider": ..., "model": ...}, ...}` |
| Validation tier | `Project.validation_tier` | `single` \| `critic` \| `consensus`, default `critic` |

**No live provider calls in any Phase 4 unit test.** `FakeProvider` is the
counterparty throughout. The live probes stay where they belong — the adapter
suites and `e2e/chat.spec.ts`.

---

## Three decisions this plan settles

The parent plan's Phase 4 table states acceptance criteria that the code as it
stands cannot satisfy. Each is resolved here rather than discovered mid-task.

### 1. Preconditions must become machine-checkable

Task 4.4 requires that "a test must prove GARCH is refused when ARCH-LM finds
no effects". But `RegisteredTool.preconditions` today is **prose aimed at the
model**:

```python
preconditions=(
    "the selected column holds one regularly observed return series in decimal units",
    "at least ~250 observations; NaNs are dropped",
)
```

Nothing there mentions ARCH effects, and nothing there is executable.

**Decision.** Add an optional `gates: tuple[Gate, ...] = ()` field to
`RegisteredTool`, populated only for the tools that have a machine-checkable
precondition. A `Gate` names a deterministic check and the verdict it demands:

```python
@dataclass(frozen=True)
class Gate:
    """A precondition an agent's tool choice must satisfy before it may run."""
    check: str            # a DiagnosticsEngine check name, or a data-shape rule
    requires: Literal["reject", "fail_to_reject", "min_obs"]
    threshold: float | None = None
    because: str = ""     # shown to the user when the gate refuses
```

Defaulting to empty means 30-odd registrations are untouched; only
`garch`/`egarch`/`gjr_garch`, `var_model`/`vecm`, and `johansen` gain one.

*Alternative considered:* a separate `agents/preconditions.py` registry keyed
by tool name. Rejected — whether GARCH needs ARCH effects is a fact about
GARCH, and splitting it from the tool means the two drift.

The prose `preconditions` stays exactly as it is: it is what the model reads,
and it is not redundant with a gate. One is guidance, the other is a refusal.

### 2. The orchestrator gets its own route, not a mode flag on the chat route

`POST /api/chats/{id}/messages` (Phase 3) streams provider tokens and is
covered by `e2e/chat.spec.ts`. Task 4.8 needs to stream *step-level progress*:
a different event vocabulary, a different failure model, and a turn that can
take minutes.

**Decision.** Add `POST /api/chats/{id}/runs`, streaming `RunEvent`s over SSE.
The Phase 3 route is left untouched. This is additive — the existing gate keeps
passing unchanged, and "plain chat" stays available for the questions that do
not warrant six model calls.

*Alternative considered:* a `mode` field on `MessageSend`. Rejected — it makes
one route's response schema depend on a request field, which neither the
frontend's `streamChat` nor its tests can narrow on.

### 3. `Diagnostic.passed` is tri-state and gates must respect it

`passed=None` means "not judged" — the invariant `CLAUDE.md` names first.
A gate that reads `None` as failure would refuse valid work; one that reads it
as success would let an unchecked assumption through.

**Decision.** `None` blocks the *gate* but not the *run*: the Econometrician
proceeds and the `PreconditionVerdict` carries `judged=False`, which the
Validator sees and the Narrator must disclose. A test asserts all three
branches.

---

## Task 4.1: Agent schemas

**Files:**
- Create: `backend/src/econometrica/agents/__init__.py`
- Create: `backend/src/econometrica/agents/schemas.py`
- Test: `backend/tests/agents/test_schemas.py`

The types every other Phase 4 task speaks. They exist before any agent does,
because the contract between agents is the thing most worth pinning down
first — and because "malformed LLM output is rejected and retried rather than
passed downstream" is a property of these types, not of the agents.

**Step 1: Write the failing test.** Cover, at minimum:

- A well-formed `AnalysisPlan` round-trips through `model_validate_json`.
- A plan step naming a tool that is not in the registry is rejected.
- A plan step whose `params` fail the tool's own `params_model` is rejected,
  and the error names the offending field.
- A plan with zero steps is rejected — an empty plan is a parse failure
  dressed as success, and it is what a model emits when it has misread the
  question.
- `DatasetSpec` rejects a window whose end precedes its start.
- `ValidationVerdict` with `approved=False` and no reasons is rejected — a
  rejection a user cannot act on is worse than none.
- `parse_agent_json` recovers a JSON object from a fenced ```json block and
  from surrounding prose, because models emit both.
- `parse_agent_json` on unrecoverable output raises `AgentOutputError`
  carrying the raw text, so the retry has something to show the model.

**Step 2: Run it.** `uv run pytest tests/agents/test_schemas.py -v` — expect
`ModuleNotFoundError: No module named 'econometrica.agents'`.

**Step 3: Implement.** The shapes:

```python
class DatasetSpec(BaseModel):
    tickers: list[str]
    start: date
    end: date
    frequency: Literal["D", "W", "M", "Q", "A"] = "D"
    return_method: Literal["simple", "log"] = "log"
    risk_free: str | None = None

class PlanStep(BaseModel):
    id: str
    tool: str                    # must resolve in the registry
    params: dict[str, Any]       # must validate against that tool's params_model
    depends_on: list[str] = []
    rationale: str = ""

class AnalysisPlan(BaseModel):
    question: str
    dataset: DatasetSpec
    steps: list[PlanStep] = Field(min_length=1)
    hypotheses: list[str] = []
    chart_intents: list[str] = []

class ValidationVerdict(BaseModel):
    approved: bool
    reasons: list[str] = []
    revise_steps: list[str] = []   # PlanStep ids
```

Validation of `tool` and `params` is a `model_validator` on `PlanStep` that
consults `get_registry()`. Cycle detection over `depends_on` is a
`model_validator` on `AnalysisPlan` — a plan whose steps cannot be ordered is
not a plan.

**Step 4: Run it.** Expect all green.

**Step 5: Commit.** `feat(agents): add typed analysis plan and verdict schemas`

---

## Task 4.2: Planner

**Files:** `agents/base.py`, `agents/planner.py`; test
`tests/agents/test_planner.py`

`agents/base.py` first: an `Agent` base holding the provider, model, role name
and the retry loop. Every agent gets malformed-output retry from one place —
ask, parse, and on `AgentOutputError` re-ask once with the parse error appended
as a user turn, then give up.

**Tests must cover:** a scripted `FakeProvider` reply becomes an
`AnalysisPlan`; the registry is offered as tool schemas in the request (assert
on `FakeProvider.calls`, not just the result); a malformed first reply followed
by a good second one succeeds in two calls; two malformed replies raise; the
retry prompt contains the parse error.

**Commit:** `feat(agents): add planner with bounded malformed-output retry`

**Landed, with one revision to the plan above.** The registry is rendered into
the *system prompt* by `agents/catalogue.py` rather than passed as native
`ToolSpec`s. Passing tools natively invites the model to emit `tool_calls`,
and a Planner's output is a plan — dependencies and rationale that a tool call
cannot carry. The test still asserts on `FakeProvider.calls`, just on the
prompt rather than the `tools` argument.

**A constraint 4.8 and 4.10 must handle.** The full catalogue is ~48k
characters, roughly 12k tokens; parameter descriptions are 17k of that, so
even a stripped rendering is ~7k. Measured against real models via
`/api/show`: **tinyllama is a 2048-token model** and qwen3-coder is 262k. So
the catalogue does not fit every local model, and `render_tool_catalogue`
takes a `families` filter for exactly this. The orchestrator should narrow to
the families a question needs rather than always sending all 36 tools — which
also plans better, since a model choosing among 36 tools chooses worse than
one choosing among six.

---

## Task 4.3: Data Steward

**Files:** `agents/data_steward.py`; test `tests/agents/test_data_steward.py`

Resolves a `DatasetSpec` into a frame plus a `DataQualityReport`. Phase 6 owns
the real market-data adapters, so this task takes an injected resolver
protocol and the tests supply a synthetic one — the agent's job is calendar
alignment (`econ.returns.align_series`), frequency conversion, return
construction and quality reporting, none of which needs a network.

**Tests must cover:** misaligned calendars are inner-joined and the dropped
count is reported; a series that starts late is flagged as a survivorship
risk; a frame whose last observation postdates the analysis window is flagged
as look-ahead; an empty overlap raises rather than returning an empty frame.

**Commit:** `feat(agents): add data steward with quality reporting`

**Landed, and it consults no model.** The design lists the Data Steward among
the six roles, but nothing it does here needs one: alignment, frequency
conversion and return construction each have exactly one right answer, and a
reproducibility manifest means nothing if the data under it varied with a
model's mood. The genuinely model-shaped part — mapping an uploaded file's
columns to roles — is Phase 6 Task 6.1, where the user confirms the mapping.

`Dataset` carries prices *and* returns because the registry wants each in
different places: the unit-root family tests levels, the volatility family
fits returns. Columns are the tickers verbatim; binding a tool's `column`
parameter to one of them is Task 4.4's job.

Watch the frequency letters: `DatasetSpec.frequency` uses `D/W/M/Q/A`, from
`econ.returns.PERIODS_PER_YEAR`, and **pandas 3 rejects `M`, `Q` and `A`
outright** — `resample("M")` raises `ValueError`, it does not warn. The
steward maps them to `ME`/`QE`/`YE`.

---

## Task 4.4: Econometrician — and the gates

**Files:** `econ/registry.py` (add `Gate`), `econ/volatility/garch.py`,
`econ/multivariate/*.py` (declare gates), `agents/econometrician.py`; tests
`tests/econ/test_gates.py`, `tests/agents/test_econometrician.py`

Binds plan steps to registry tools and **refuses** those that violate a gate.

**Tests must cover:**
- `garch` is refused on a series where ARCH-LM fails to reject — the parent
  plan's named acceptance test. Use `tests/econ/fixtures.py`:
  `make_stationary_ar1` has no ARCH effects, `make_garch_series` does.
- `garch` is accepted on `make_garch_series`.
- `var_model` is refused on `make_random_walk` (non-stationary) and accepted
  on `make_stationary_ar1`.
- A gate whose diagnostic returns `passed=None` yields `judged=False` and does
  **not** refuse — decision 3 above.
- A refusal names the tool, the check and the `because` text.
- The refusal is a typed result, not an exception: the orchestrator has to be
  able to hand it to the Validator.

**Commit:** `feat(agents): add econometrician with executable tool preconditions`

**Landed, and it consults no model either.** By the time a plan arrives,
`PlanStep` has proved every tool exists and every parameter is one it accepts;
what remains is enforcing gates and running tools, neither of which is a
question to ask a model. That makes three of the six roles deterministic —
Data Steward, Econometrician, and the grounding gate — leaving Planner,
Validator, Narrator (and Phase 5's Visualizer) as the model-assignable ones.
**This narrows what `Project.model_assignments` means and is worth revisiting
if per-role assignment for all six is wanted.**

**A statistical bug the red-first run caught.** The first `arch_effects` gate
ran ARCH-LM on the raw series and *allowed* GARCH on a homoskedastic AR(1) —
the named acceptance case. ARCH-LM regresses squared values on their own lags,
so a series autocorrelated in the *mean* fails it whether or not its variance
moves. The diagnostics engine documents itself as running over a fitted
model's residuals; a gate handed a raw series has to supply the fit, so it
pre-whitens with an AR(1) first. A test covers two phi values, so it holds for
the mechanism rather than for one seed.

`Dataset.frame` was added to close the seam: `ToolFn` takes one DataFrame, so
levels and returns coexist in it and the column name (`AAA` vs `AAA_return`)
carries the distinction a plan step needs.

---

## Task 4.5: Validator

**Files:** `agents/validator.py`; test `tests/agents/test_validator.py`

Consumes the plan, the `ResultSet`s, the deterministic diagnostics and any
precondition refusals, and emits a `ValidationVerdict`.

**Tests must cover:** diagnostics are passed as facts in the prompt (assert
the ARCH-LM statistic appears in the message text the fake received — the
Validator must never be asked to infer it); an approval with reasons; a
rejection naming step ids that exist in the plan; a rejection naming an
unknown step id is itself rejected as malformed; **the orchestrator warns when
the Validator and Econometrician resolve to the same provider**, which is the
parent plan's named criterion — independence is the whole point of the role.

**Commit:** `feat(agents): add validator fed by deterministic diagnostics`

**Landed.** `independence_warning()` lives here rather than in the
orchestrator so it is testable before 4.8 exists; the orchestrator will call
it. It returns `None` when either side has no model, since the Econometrician
is deterministic and nothing is being marked against itself.

`Agent.check()` was added to the base class for this: a Validator may only ask
for revisions to steps that exist, which the schema cannot express because
only the plan knows which those are. Raising `ValueError` from it spends a
retry, so an unactionable rejection gets one chance to become actionable
before the run gives up.

Diagnostics render with their verdict spelled out, and the word for
`passed=None` is **"not judged"**, never "failed". A model told a check failed
when it merely did not run rejects work for a reason that does not exist.

---

## Task 4.6: The numeric grounding gate

**Files:** `agents/grounding.py`; test `tests/agents/test_grounding.py`

The single most important safeguard in the system (design §4). Extracts every
number from narrator prose and matches it against
`ResultSet.all_numeric_values()`.

The hard part is not extraction, it is **what is exempt**. Getting this wrong
in either direction is bad: too strict and every honest sentence is blocked,
too loose and the gate is theatre.

**Tests must cover:**
- A fabricated statistic is blocked.
- A correctly rounded value passes — `1.2977…` cited as `1.30`.
- A value restated as a percentage passes — `0.83` cited as `83%`.
- Years are exempt (`in 2008`), and a year that is *not* in a date context is
  not exempt.
- Sample sizes matching an integer count present in the result are exempt.
- List ordinals at line start (`1.`, `2.`) are exempt.
- A number inside a cited artifact id is exempt (`figure 2`).
- The block report names the offending number and its sentence, so the
  revision prompt can be specific.

Tolerance is relative, not absolute: `0.0001` and `1_000_000` cannot share an
epsilon.

**Commit:** `feat(agents): add numeric grounding gate`

**Landed.** Precision comes from the citation rather than a global epsilon:
"1.30" claims two decimal places and matches anything rounding to it at two,
"1.3" claims one. Stricter and more permissive than a fixed tolerance, in the
right directions.

`allowed_values()` unions `ResultSet.all_numeric_values()` with the numbers in
`ResultSet.params`, which the former does not cover. Without that, "a
GARCH(1,1) fit" reads as two fabrications.

Exemptions are years in a date context, markdown list markers, artifact
references, model orders inside a name, and conventional significance levels
where the sentence is about significance. **Each has a paired test proving it
does not apply outside its context** — "the statistic is 2008" is checked,
"returns rose 5%" is checked, and a real figure opening a line is checked.
That pairing is the whole discipline here: an over-strict gate that blocks
"significant at the 5% level" gets switched off within a day, and a gate that
is off protects nothing.

Verified on realistic narrator prose beyond the unit tests: an honest GARCH
paragraph passes with six numbers checked, and a transposed digit
(0.8834 → 0.8843) is caught.

---

## Task 4.7: Narrator

**Files:** `agents/narrator.py`; test `tests/agents/test_narrator.py`

Writes the interpretation, cites artifact and statistic identifiers, and its
output passes Task 4.6's gate — enforced in the agent, not merely hoped for:
a blocked draft is re-asked once with the offending numbers listed, and a
second failure returns the verdict rather than the prose.

**Commit:** `feat(agents): add narrator gated on numeric grounding`

**Landed.** The output type is `Narrative` — `{prose, citations}` — rather than
raw text, so the base class's JSON parsing and retry apply unchanged and the
grounding check slots into `Agent.check()`. Failing that hook spends a retry
with the offending figures named, which is the only version of the retry worth
having.

`write()` returns a `Narration`, not prose: `published=False` with the
grounding report attached when every draft cited something invented. Results
without prose are inconvenient; prose with an invented statistic is what this
application exists to prevent, so withholding is the correct failure.

**What 4.8 inherits.** Every piece it needs now exists: `Planner.plan()`,
`DataSteward.resolve()` → `Dataset.frame`, `Econometrician.run()` →
`ExecutionReport`, `Validator.review()` + `independence_warning()`, and
`Narrator.write()`. What is left is the tier logic, the bounded revision loop,
the `RunEvent` vocabulary and the SSE route.

---

## Task 4.8: Orchestrator

**Files:** `agents/orchestrator.py`, `api/routers/runs.py`, `schemas/run.py`;
tests `tests/agents/test_orchestrator.py`, `tests/api/test_runs.py`

Runs the pipeline, honours the three tiers, bounds revision loops, and streams
`RunEvent`s over `POST /api/chats/{id}/runs` (decision 2).

**Tests must cover:** `single` skips the Validator but still runs both
deterministic gates; `critic` runs it; `consensus` runs the plan on N providers
and surfaces a diff rather than picking a winner; a rejected verdict triggers
exactly one revision and a second rejection ends the run rather than looping;
step-level events arrive in dependency order; a provider failure mid-run leaves
a readable, persisted run rather than a half-written one — the same contract
`messages.py` already keeps.

**Commit:** `feat(agents): add orchestrator with tiered validation and sse progress`

**Landed**, on `POST /api/chats/{id}/runs` as decision 2 said. Roles bind from
`Project.model_assignments`, the tier from `Project.validation_tier`, both
validated before anything runs. `get_price_source` is the seam Phase 6 fills;
until then it refuses with an explanation rather than returning empty frames.

**The tier decides, not the wiring.** A project set to `single` gets no review
even when a Validator is configured — otherwise "cheapest tier" would depend
on how the orchestrator happened to be constructed.

**Two integration bugs that only a live probe could find.** Both were invisible
to the whole scripted test suite, because a scripted reply is one written by
someone who already knows the answer.

1. *Two vocabularies for one concept.* `DatasetSpec.return_method` takes
   `"log"`; the tool-level `transform` in the same catalogue takes
   `"log_diff"`. A real model reached for the tool spelling **every time**,
   burning a retry on a synonym. `log_diff` is now accepted as what it is —
   a log difference is a log return. Planning cost halved: 2 attempts and
   7829 input tokens became 1 and 3502. (`diff` is still rejected; a price
   difference is not a simple return.)
2. *The Planner did not know the column names.* Data is assembled **after**
   planning, from the tickers the plan requests, so columns are `BTC-USD` and
   `BTC-USD_return` — never the tools' `price`/`return` defaults, which is
   what the model used. Every step of every real plan would have failed at
   execution. The convention is now stated in the system prompt.

**On catalogue narrowing:** measured, not assumed. The full 36-tool catalogue
costs 27.8k input tokens against 3.7k for one family, with comparable plan
quality on a 262k-context model. So narrowing is a cost optimisation for large
models and a hard requirement for small ones — not a correctness issue where
the context fits.

---

## Task 4.9: Run and Step persistence

**Files:** `db/models/run.py`, `services/tracing.py`, an Alembic revision;
tests `tests/db/test_run_model.py`, `tests/services/test_tracing.py`

A `Run` per assistant turn holding a DAG of `Step`s: agent, provider, model,
tokens, cost, latency, tool-call hashes, parent links.

Two things `CLAUDE.md` warns about and this task will hit:

- Order steps on an identity column, **never** `created_at` — steps written in
  one transaction tie exactly.
- Alembic does not autogenerate CHECK constraints. Hand-write them and add a
  test, because `alembic check` cannot verify them either.

**Commit:** `feat(db): persist runs and steps for the agent trace`

**Landed**, plus `GET /api/chats/{id}/runs` and `GET /api/runs/{id}` to read a
trace back. Steps are written after the stream ends, not during it: a trace is
only complete once the run is, and a write that failed mid-stream would leave
a partial trace claiming to be whole. A failure to record emits `run.untraced`
rather than retracting a run the client already watched succeed.

**One warning in this document was half right.** Alembic's autogenerate *does*
emit CHECK constraints when it **creates** a table — all thirteen appeared
without editing. What it cannot see is one added to or changed on a table that
already exists, which is the `validation_tier` case. `alembic check` verifies
neither, so `tests/db/test_migrations.py` now asserts every constraint in the
models reaches some migration, and `tests/db/test_run_model.py` exercises each
one against real Postgres.

**A gap the trace tests exposed.** `Narrator.write()` returns a `Narration`,
not an `AgentResult`, so its retries were invisible — and the Narrator is the
agent most likely to retry, since the grounding gate rejects drafts. A draft
withheld by that gate still cost tokens, so `AgentAttemptsExhaustedError` now
carries its completions and `Narration` passes them through. Without it the
cost dashboard would have understated precisely the runs where the safeguard
did its job.

---

## Task 4.10: Phase 4 e2e

**Files:** `frontend/e2e/analysis.spec.ts`

"Test whether Bitcoin follows a random walk" end to end, with a full trace.

Follow `e2e/chat.spec.ts`: skip with a reason when Ollama is down, prefer a
small model, assert on the SSE wire and not only the DOM. One difference —
a six-role pipeline on a local model is slow, so the run needs its own timeout
and the tier should be pinned to `single` unless a second provider is
configured.

**Commit:** `test(e2e): close phase 4 with a full analysis run`

---

## Phase 4 definition of done

- `uv run pytest` green; `ruff check` and `mypy src` clean.
- `npx vitest run`, `npx tsc --noEmit` and `npm run test:e2e` green.
- A fabricated number in narrator prose cannot reach the user — proven by
  test, not by inspection.
- The Validator can be, and by default is, a different vendor than the
  Econometrician.
- Every number on screen traces to a `ResultSet` with a manifest.
