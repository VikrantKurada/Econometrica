# Model-Written Search Query — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the user's question into up to three symbol-shaped search queries with a small model agent before planning, so the Planner is shown real tickers (`^NSEI`) instead of inventing them — closing the gap CLAUDE.md records under "Web search runs, as of 2026-07-30".

**Architecture:** A new `QueryWriter` agent (built exactly like the other agents in `agents/`) turns the question into search queries; `Orchestrator._search_context` searches those instead of the verbatim question and feeds the attributed results to the Planner as it does today. The writer is a new **dedicated model role** (`query_writer` in `Project.model_assignments`), bound in the runs router and passed to the orchestrator like `searcher`. It degrades to the verbatim question whenever it is absent, misconfigured, or unable to answer, so the feature is strictly additive and can never fail a run.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy + Alembic, Postgres/TimescaleDB, pytest (async, `asyncio_mode=auto`), `uv` for all backend commands.

**Design note:** `docs/plans/2026-07-31-econometrica-search-query-design.md`.

## Global Constraints

- **TDD, strictly.** Write the failing test, run it, watch it fail with the *expected* error, then implement. Never write implementation first.
- **Backend commands run under `uv`** from `backend/`: `uv run pytest -q`, `uv run ruff check src tests alembic`, `uv run mypy src`, `uv run alembic upgrade head`, `uv run alembic check`.
- **DB tests need Postgres:** `docker compose up -d db --wait` before any `tests/db` or `tests/api` run.
- **`mypy --strict` clean on `src`; ruff clean on `src tests alembic`.** No new violations.
- **Commit with `git commit -F <file>` from the Bash tool**, message written via heredoc — PowerShell mangles `-m` when the body has punctuation, and `Set-Content -Encoding utf8` adds a BOM into the subject. End every commit body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **The one invariant holds:** the query writer selects no numbers. Web search is never a source of numbers — the grounding gate is unchanged, and the **Narrator still never sees search results**. Do not route them to it.
- **Branch:** all work stays on `feat/foundation`.
- **Max queries per run:** `MAX_SEARCH_QUERIES = 3`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/econometrica/agents/query_writer.py` | New. `SearchQuery` output model + `QueryWriter` agent. | 1 |
| `tests/agents/test_query_writer.py` | New. The agent in isolation. | 1 |
| `src/econometrica/db/models/run.py` | Modify. Add `query_writer` to `STEP_AGENTS`. | 2 |
| `src/econometrica/agents/trace.py` | Modify. Add `query_writer` to `AGENTS` (documentation parity). | 2 |
| `alembic/versions/f0a1c2d3e4b5_query_writer_step_agent.py` | New. Widen `ck_run_steps_agent_known`. | 2 |
| `tests/db/test_run_model.py` | Modify. A `query_writer` step is accepted. | 2 |
| `src/econometrica/agents/orchestrator.py` | Modify. `query_writer` param; `_search_context` rewrite. | 3 |
| `tests/agents/test_orchestrator.py` | Modify. Rewrite the four web-search tests; add new ones. | 3 |
| `src/econometrica/api/routers/runs.py` | Modify. Bind the `query_writer` role in `_build`. | 4 |
| `tests/api/test_runs.py` | Modify. Add the model-written-query and degradation tests. | 4 |
| `tests/agents/test_live_query_writer.py` | New. `@pytest.mark.live` probe on the NSEI case. | 5 |
| `CLAUDE.md`, `README.md` | Modify. Record the gap is closed. | 6 |

---

## Task 1: The `QueryWriter` agent

**Files:**
- Create: `src/econometrica/agents/query_writer.py`
- Test: `tests/agents/test_query_writer.py`

**Interfaces:**
- Consumes: `econometrica.agents.base.Agent`, `AgentResult`; `econometrica.llm.base.LLMProvider`; `econometrica.llm.types.Message`.
- Produces:
  - `class SearchQuery(BaseModel)` with `queries: list[str]`; its validator strips each entry, drops empties, de-duplicates case-insensitively, and raises `ValueError` if nothing survives.
  - `class QueryWriter(Agent[SearchQuery])`, `role = "query_writer"`, `__init__(self, provider, model, *, max_attempts=2)`, `output_model() -> type[SearchQuery]`, and `async def write(self, question: str) -> AgentResult[SearchQuery]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agents/test_query_writer.py`:

```python
"""The Query Writer: a question becomes symbol-shaped lookup queries."""

import json

from econometrica.agents.query_writer import QueryWriter, SearchQuery
from econometrica.llm.fake import FakeProvider


def writer(*responses: str) -> tuple[QueryWriter, FakeProvider]:
    fake = FakeProvider(name="q", responses=list(responses))
    return QueryWriter(fake, "fake-1"), fake


async def test_it_writes_a_symbol_shaped_query_for_a_named_index():
    agent, fake = writer(json.dumps({"queries": ["Nifty 50 ticker symbol Yahoo Finance"]}))

    result = await agent.write(
        "How has the National Stock Exchange of India grown over the last 10 years?"
    )

    assert result.output.queries == ["Nifty 50 ticker symbol Yahoo Finance"]
    # The question has to reach the model — the whole point is extracting the
    # instrument name from it.
    sent = "\n".join(m.content for m in fake.calls[0].messages)
    assert "National Stock Exchange of India" in sent


async def test_an_empty_query_list_is_rejected_and_retried():
    agent, fake = writer(
        json.dumps({"queries": []}),
        json.dumps({"queries": ["AAPL ticker symbol Yahoo Finance"]}),
    )

    result = await agent.write("What is Apple's beta?")

    assert result.output.queries == ["AAPL ticker symbol Yahoo Finance"]
    assert len(fake.calls) == 2  # the empty reply spent a retry


async def test_blank_and_duplicate_queries_are_stripped_and_deduped():
    agent, _ = writer(
        json.dumps({"queries": ["  AAPL ticker  ", "AAPL TICKER", "", "   "]})
    )

    result = await agent.write("Apple")

    assert result.output.queries == ["AAPL ticker"]


async def test_one_query_per_instrument_for_a_multi_instrument_question():
    agent, _ = writer(
        json.dumps(
            {"queries": ["Nifty 50 ticker symbol Yahoo Finance", "AAPL ticker symbol"]}
        )
    )

    result = await agent.write("How does AAPL compare with the Nifty 50?")

    assert result.output.queries == [
        "Nifty 50 ticker symbol Yahoo Finance",
        "AAPL ticker symbol",
    ]


def test_the_schema_rejects_an_all_blank_list_directly():
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        SearchQuery(queries=["", "   "])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/agents/test_query_writer.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'econometrica.agents.query_writer'`.

- [ ] **Step 3: Write the implementation**

Create `src/econometrica/agents/query_writer.py`:

```python
"""The Query Writer: a question becomes symbol-shaped lookup queries.

Before an analysis is planned, this turns the user's prose into short web
searches whose only job is to surface the exact ticker or symbol of each
instrument the question names. The motivation is measured, not supposed: the
verbatim question "How has the National Stock Exchange of India grown…" returns
market commentary and no symbol, while "Nifty 50 ticker symbol Yahoo Finance"
returns `^NSEI` as the top hit. The extraction of "Nifty 50" from the prose is
the part no string transform can do and a model can, which is why this is a
billed turn.

It selects nothing numeric and states no finding — it writes queries. What its
queries find is fed to the Planner, never the Narrator, and is never a source of
numbers: the grounding gate is unchanged.
"""

from pydantic import BaseModel, Field, field_validator

from econometrica.agents.base import Agent, AgentResult
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message

_SYSTEM = """\
You are the Query Writer in an econometrics workbench. Before an analysis is
planned, you turn the user's question into short web-search queries whose only
job is to surface the exact ticker or symbol of each financial instrument the
question is about — an index, a stock, a currency pair, a commodity — in the
form a market-data vendor uses (for example ^NSEI, BTC-USD, ^GSPC).

A good query names the instrument by its common name and asks for its symbol,
e.g. "Nifty 50 ticker symbol Yahoo Finance" or "Brent crude oil Yahoo Finance
symbol". A whole analytical question ("how has it grown over ten years") is a
poor query: it returns commentary, not a symbol.

Write one query per distinct instrument the question names. If the question
already gives an explicit ticker, you may still write a query to confirm it.

Reply with a single JSON object and nothing else:

{"queries": ["<instrument name> ticker symbol Yahoo Finance"]}\
"""


class SearchQuery(BaseModel):
    """The queries to run, cleaned. Local to this module: it is consumed inside
    `_search_context` and never passed downstream, so it is not a cross-agent
    contract that belongs in `agents/schemas.py`."""

    queries: list[str] = Field(default_factory=list)

    @field_validator("queries", mode="after")
    @classmethod
    def clean(cls, value: list[str]) -> list[str]:
        # Strip, drop empties, de-duplicate case-insensitively. Raising when
        # nothing survives spends a retry rather than the run — the same path a
        # malformed reply takes, since the base loop treats a ValueError here
        # exactly as it treats a parse failure.
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            key = text.lower()
            if text and key not in seen:
                seen.add(key)
                cleaned.append(text)
        if not cleaned:
            raise ValueError("at least one non-empty search query is required")
        return cleaned


class QueryWriter(Agent[SearchQuery]):
    role = "query_writer"

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        super().__init__(provider, model, max_attempts=max_attempts)

    def output_model(self) -> type[SearchQuery]:
        return SearchQuery

    async def write(self, question: str) -> AgentResult[SearchQuery]:
        return await self.ask(
            [Message.system(_SYSTEM), Message.user(f"Question: {question}")]
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/agents/test_query_writer.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src tests` and `uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/c1.txt <<'EOF'
feat(agents): a query writer turns a question into ticker lookups

The verbatim question is a poor search query -- measured, it returns
market commentary and no symbol, while "Nifty 50 ticker symbol Yahoo
Finance" returns ^NSEI. Extracting the instrument name from the prose is
the part a string transform cannot do, so this is a small model agent:
question in, up to a few symbol-shaped lookup queries out. It selects
nothing numeric and states no finding, and its output is cleaned
(stripped, de-duplicated, non-empty) at the schema boundary so a useless
reply spends a retry rather than the run.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add src/econometrica/agents/query_writer.py tests/agents/test_query_writer.py
git commit -F /tmp/c1.txt && rm -f /tmp/c1.txt
```

---

## Task 2: Allow `query_writer` as a trace step agent

The writer's billed LLM turn is recorded with `agent="query_writer"`, so that value must be allowed to land in `run_steps`. This is the `quant_coder` trap CLAUDE.md records: widening `STEP_AGENTS` alone leaves a fresh database rejecting the step while the `create_all`-built unit suite stays green, so a hand-written migration is required and two tests gate it.

**Files:**
- Modify: `src/econometrica/db/models/run.py:49-56` (`STEP_AGENTS`)
- Modify: `src/econometrica/agents/trace.py:27-37` (`AGENTS`, documentation parity)
- Create: `alembic/versions/f0a1c2d3e4b5_query_writer_step_agent.py`
- Test: `tests/db/test_run_model.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `"query_writer"` is a legal value of `run_steps.agent` in the models, the migrations, and a fresh database.

- [ ] **Step 1: Write the failing DB test**

Add to `tests/db/test_run_model.py` (beside `test_a_quant_coder_step_is_accepted`):

```python
async def test_a_query_writer_step_is_accepted(session):
    """The query writer's billed turn has to reach the trace.

    Exercises the *model's* constraint against Postgres. Whether the
    hand-written migration widening `ck_run_steps_agent_known` exists is
    `test_every_value_in_a_check_constraint_vocabulary_reaches_a_migration`'s
    job — the test database is built with `create_all`, not the migrations.
    """
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="query_writer", kind="llm", status="ok"))

    await session.flush()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `docker compose up -d db --wait` then `uv run pytest tests/db/test_run_model.py::test_a_query_writer_step_is_accepted -q`
Expected: FAIL — the flush raises `IntegrityError` (violates `ck_run_steps_agent_known`), because `STEP_AGENTS` does not yet contain `query_writer`.

- [ ] **Step 3: Widen the model vocabulary**

In `src/econometrica/db/models/run.py`, add `query_writer` to `STEP_AGENTS`:

```python
STEP_AGENTS = (
    "planner",
    "data_steward",
    "econometrician",
    "validator",
    "narrator",
    "quant_coder",
    "query_writer",
)
```

In `src/econometrica/agents/trace.py`, add it to `AGENTS` for parity (nothing validates against this tuple, but it documents the roles a trace may carry):

```python
AGENTS = (
    "planner",
    "data_steward",
    "econometrician",
    "validator",
    "narrator",
    "quant_coder",
    # Written before planning: turns the question into symbol-shaped search
    # queries so the Planner is shown real tickers instead of inventing them.
    "query_writer",
)
```

- [ ] **Step 4: Run the DB test to verify it passes**

Run: `uv run pytest tests/db/test_run_model.py::test_a_query_writer_step_is_accepted -q`
Expected: PASS (the test DB is `create_all`, so the model change alone satisfies it).

- [ ] **Step 5: Confirm the migration gate now fails**

Run: `uv run pytest tests/db/test_migrations.py::test_every_value_in_a_check_constraint_vocabulary_reaches_a_migration -q`
Expected: FAIL — `query_writer` is in `STEP_AGENTS` but in no migration file.

- [ ] **Step 6: Write the hand-written migration**

Create `alembic/versions/f0a1c2d3e4b5_query_writer_step_agent.py` (structure copied verbatim from `b3a17c0d9e42_quant_coder_step_agent.py`; head confirmed as `b3a17c0d9e42` via `uv run alembic heads`):

```python
"""allow query_writer as a run step agent

Revision ID: f0a1c2d3e4b5
Revises: b3a17c0d9e42
Create Date: 2026-07-31 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0a1c2d3e4b5"
down_revision: str | Sequence[str] | None = "b3a17c0d9e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_AGENTS = (
    "planner",
    "data_steward",
    "econometrician",
    "validator",
    "narrator",
    "quant_coder",
    "query_writer",
)
_PREVIOUS = _AGENTS[:-1]


def _in_list(values: Sequence[str]) -> str:
    return "agent IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    """Add `query_writer` to the agents a step may name.

    Hand-written for the reason CLAUDE.md records: autogenerate emits CHECK
    constraints only when it *creates* a table and sees nothing when one changes
    on a table that already exists, so this revision came out empty. `alembic
    check` cannot see it either — the gate is `tests/db/test_run_model.py`, which
    inserts a `query_writer` step against the real database, plus the value test
    in `tests/db/test_migrations.py`.

    Dropped and recreated rather than altered: Postgres has no `ALTER
    CONSTRAINT` for a CHECK expression.
    """
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_AGENTS))


def downgrade() -> None:
    """Remove the query_writer steps first.

    Narrowing the constraint over rows that already violate it would fail the
    migration outright. The rows are deleted because there is nowhere honest to
    move them.
    """
    op.execute("DELETE FROM run_steps WHERE agent = 'query_writer'")
    op.drop_constraint("ck_run_steps_agent_known", "run_steps", type_="check")
    op.create_check_constraint("ck_run_steps_agent_known", "run_steps", _in_list(_PREVIOUS))
```

- [ ] **Step 7: Verify the migration gate passes and the chain is intact**

Run:
```bash
uv run pytest tests/db/test_migrations.py -q
uv run alembic upgrade head
uv run alembic check
```
Expected: the value test PASSES; `upgrade head` applies `f0a1c2d3e4b5`; `alembic check` reports no drift.

- [ ] **Step 8: Full DB suite + lint**

Run: `uv run pytest tests/db -q` and `uv run ruff check alembic src`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
cat > /tmp/c2.txt <<'EOF'
feat(db): allow query_writer as a run step agent

The query writer's turn is billed and must be attributable, so its trace
step is agent="query_writer". That value has to be legal in run_steps.
Widening STEP_AGENTS alone is the quant_coder trap: the create_all unit
DB accepts it while a migrated database rejects every such step, because
autogenerate cannot see a CHECK changed on an existing table. So a
hand-written revision widens ck_run_steps_agent_known, and the two gates
that caught quant_coder -- a real insert against Postgres and the
constraint-value test -- catch this too.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add src/econometrica/db/models/run.py src/econometrica/agents/trace.py \
  alembic/versions/f0a1c2d3e4b5_query_writer_step_agent.py tests/db/test_run_model.py
git commit -F /tmp/c2.txt && rm -f /tmp/c2.txt
```

---

## Task 3: `_search_context` uses the writer

The orchestrator gains one constructor parameter and rewrites `_search_context` to write queries, search each, and concatenate. Search steps stay `agent="planner"` (they feed the planner); only the writer's LLM turn is `agent="query_writer"`.

**Files:**
- Modify: `src/econometrica/agents/orchestrator.py` (imports, `__init__`, `_search_context`, new `_search_queries`, `MAX_SEARCH_QUERIES`)
- Test: `tests/agents/test_orchestrator.py` (`build()` helper + the web-search section)

**Interfaces:**
- Consumes: `QueryWriter` from Task 1; `AgentAttemptsExhaustedError`, `AgentRefusedError` from `econometrica.agents.base`.
- Produces: `Orchestrator.__init__(..., query_writer: QueryWriter | None = None)`; a `_search_context` that searches model-written queries when a writer is present and the verbatim question otherwise.

- [ ] **Step 1: Update the `build()` helper and rewrite the web-search tests**

In `tests/agents/test_orchestrator.py`, add the import:

```python
from econometrica.agents.query_writer import QueryWriter
```

Extend `build()` — add the parameter and wire the writer:

```python
def build(
    *,
    plans: list[str] | None = None,
    verdicts: list[str] | None = None,
    prose: list[str] | None = None,
    tier: str = "critic",
    planner_providers: list[FakeProvider] | None = None,
    source: object | None = None,
    searcher: object | None = None,
    web_search: bool = False,
    query_writer_provider: FakeProvider | None = None,
) -> tuple[Orchestrator, dict[str, FakeProvider]]:
    planner_fakes = planner_providers or [
        FakeProvider(name="p", responses=plans or [json.dumps(PLAN)])
    ]
    validator_fake = FakeProvider(name="v", responses=verdicts or [APPROVED])
    narrator_fake = FakeProvider(name="n", responses=prose or [narrative()])
    query_writer = (
        QueryWriter(query_writer_provider, "fake-1")
        if query_writer_provider is not None
        else None
    )

    orchestrator = Orchestrator(
        planners=[Planner(fake, "fake-1") for fake in planner_fakes],
        steward=DataSteward(source or FakeSource(), min_obs=30),
        validator=Validator(validator_fake, "fake-1"),
        narrator=Narrator(narrator_fake, "fake-1"),
        tier=tier,
        searcher=searcher,
        web_search=web_search,
        query_writer=query_writer,
    )
    fakes = {
        "planner": planner_fakes[0],
        "validator": validator_fake,
        "narrator": narrator_fake,
    }
    if query_writer_provider is not None:
        fakes["query_writer"] = query_writer_provider
    return orchestrator, fakes
```

Replace the entire web-search section (from `# --- web search ---` to the end of the file) with the following. `SpyProvider`, `a_result`, `planner_prompt`, and `search_step` are unchanged; the tests below replace the four old ones and add the new cases. Add a `QUERY` constant near the top:

```python
QUERY = "Nifty 50 ticker symbol Yahoo Finance"


def query_writer_step(outcome):
    return next(
        s for s in outcome.trace if s.agent == "query_writer" and s.kind == "llm"
    )


async def test_a_model_written_query_is_searched_not_the_verbatim_question():
    provider = SpyProvider([a_result()])
    orchestrator, fakes = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(name="q", responses=[json.dumps({"queries": [QUERY]})]),
    )

    await orchestrator.run(QUESTION)

    assert provider.asked == [QUERY]  # the generated query, not QUESTION
    assert "^NSEI" in planner_prompt(fakes)  # the results still reach the Planner


async def test_without_a_query_writer_the_verbatim_question_is_the_floor():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(searcher=provider, web_search=True)  # no writer

    await orchestrator.run(QUESTION)

    assert provider.asked == [QUESTION]


async def test_at_most_three_queries_are_searched():
    provider = SpyProvider([a_result()])
    five = {"queries": [f"q{n} ticker symbol" for n in range(5)]}
    orchestrator, _ = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(name="q", responses=[json.dumps(five)]),
    )

    await orchestrator.run(QUESTION)

    assert len(provider.asked) == 3


async def test_a_writer_that_will_not_answer_falls_back_to_the_verbatim_question():
    provider = SpyProvider([a_result()])
    # Two unparseable replies exhaust the writer's attempts.
    orchestrator, _ = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(
            name="q", responses=["not json", "still not json"]
        ),
    )

    outcome = await orchestrator.run(QUESTION)

    assert provider.asked == [QUESTION]
    assert outcome.status == "completed"


async def test_a_disabled_search_never_reaches_the_provider():
    """Asserted on the spy, not the outcome: a provider called and then
    discarded would pass an outcome-level check while being the bug."""
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider,
        web_search=False,
        query_writer_provider=FakeProvider(name="q", responses=[json.dumps({"queries": [QUERY]})]),
    )

    await orchestrator.run(QUESTION)

    assert provider.asked == []


async def test_a_failed_search_degrades_the_run_rather_than_failing_it():
    provider = SpyProvider(boom="the endpoint returned 503")
    orchestrator, fakes = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(name="q", responses=[json.dumps({"queries": [QUERY]})]),
    )

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert "read from the web" not in planner_prompt(fakes)
    step = search_step(outcome)
    assert step.status == "failed"
    assert "503" in step.detail


async def test_the_search_step_records_the_query_it_ran():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(name="q", responses=[json.dumps({"queries": [QUERY]})]),
    )

    outcome = await orchestrator.run(QUESTION)

    step = search_step(outcome)
    assert step.kind == "tool"
    assert step.agent == "planner"  # the search feeds the planner
    assert step.prompt == QUERY  # the query it ran, not the question
    assert "^NSEI" in step.response


async def test_the_writer_turn_precedes_the_search_and_the_plan():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(
        searcher=provider,
        web_search=True,
        query_writer_provider=FakeProvider(name="q", responses=[json.dumps({"queries": [QUERY]})]),
    )

    outcome = await orchestrator.run(QUESTION)

    writer = outcome.trace.index(query_writer_step(outcome))
    search = outcome.trace.index(search_step(outcome))
    plan = next(
        i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm"
    )
    assert writer < search < plan
```

- [ ] **Step 2: Run the web-search tests to verify they fail**

Run: `uv run pytest tests/agents/test_orchestrator.py -k "search or writer or query" -q`
Expected: FAIL — `Orchestrator.__init__() got an unexpected keyword argument 'query_writer'` (the parameter does not exist yet).

- [ ] **Step 3: Add the constructor parameter**

In `src/econometrica/agents/orchestrator.py`, add the imports:

```python
from econometrica.agents.base import (
    AgentAttemptsExhaustedError,
    AgentRefusedError,
    PROMPT_LIMIT,
)
from econometrica.agents.query_writer import QueryWriter
```

(Replace the existing `from econometrica.agents.base import PROMPT_LIMIT` line.) Add the module constant near the top:

```python
#: The most queries a run will search. A question naming two instruments needs
#: two lookups; a small headroom is cheap. Above this is a runaway model, not a
#: real question, and each query is a live call against a contract-free endpoint.
MAX_SEARCH_QUERIES = 3
```

Add the parameter to `__init__` (after `web_search: bool = False,`):

```python
        query_writer: QueryWriter | None = None,
```

and store it (after `self.web_search = web_search`):

```python
        #: Writes the search queries when present. Absent means the verbatim
        #: question is searched — the floor this feature never does worse than.
        #: Passed in for the same reason `searcher` is: `agents/` decides nothing
        #: about projects; the router supplies one only when the role is assigned.
        self.query_writer = query_writer
```

- [ ] **Step 4: Rewrite `_search_context` and add `_search_queries`**

Replace the body of `_search_context` (from the early-return onward) so it loops over the written queries:

```python
    async def _search_context(self, question: str, context: str, trace: TraceBuilder) -> str:
        """Web results as extra context for the Planner, or the context unchanged.

        The Planner is the agent that benefits: it picks tickers and a window
        out of prose, and the failures this exists to reduce are exactly that —
        a Planner invented `LON` for London real estate and `NSEI` for the Nifty
        50 (the real symbol is `^NSEI`), and both runs died in the Data Steward.

        The verbatim question is a poor query for that — it returns commentary,
        not a symbol — so a query writer turns it into symbol-shaped lookups
        first. Deliberately not offered to the Narrator: its output is what the
        grounding gate judges, the gate withholds a whole narration over one
        number it cannot match, and web snippets are dense with numbers.
        """
        if not (self.web_search and self.searcher is not None):
            return context

        blocks: list[str] = []
        for query in await self._search_queries(question, trace):
            outcome = await search(query, provider=self.searcher, enabled=self.web_search)
            record = outcome.to_step_record()
            record.prompt = query[:PROMPT_LIMIT]
            record.response = outcome.as_context()[:PROMPT_LIMIT]
            trace.add(record)
            found = outcome.as_context()
            if found:
                blocks.append(found)

        if not blocks:
            # A failed or empty search leaves the context alone rather than
            # appending a bare header, which would tell the model a search ran
            # and found nothing — a different claim from no search at all.
            return context
        combined = "\n\n".join(blocks)
        return f"{context}\n\n{combined}" if context else combined

    async def _search_queries(self, question: str, trace: TraceBuilder) -> list[str]:
        """The queries to search: model-written when a writer is configured,
        the verbatim question otherwise.

        A writer that refuses or cannot produce a usable reply falls back to the
        question. Search is context; losing it — or the whole run — to a query
        writer that would not answer is the worse trade.
        """
        if self.query_writer is None:
            return [question]
        try:
            result = await self.query_writer.write(question)
        except (AgentAttemptsExhaustedError, AgentRefusedError):
            return [question]

        trace.add_agent_turn(
            result,
            agent="query_writer",
            provider=getattr(self.query_writer.provider, "name", None),
            model=self.query_writer.model,
            parent=trace.last,
        )
        return result.output.queries[:MAX_SEARCH_QUERIES] or [question]
```

- [ ] **Step 5: Run the web-search tests to verify they pass**

Run: `uv run pytest tests/agents/test_orchestrator.py -q`
Expected: PASS (the whole orchestrator file, including the rewritten web-search section).

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src tests` and `uv run mypy src`
Expected: clean. (If `PROMPT_LIMIT` or an import is reported unused, adjust the import line.)

- [ ] **Step 7: Commit**

```bash
cat > /tmp/c3.txt <<'EOF'
feat(runs): search model-written queries before planning

_search_context searched the question verbatim, which returns market
commentary and no ticker. When a query writer is configured it now writes
up to three symbol-shaped lookups and searches those, so the Planner is
shown ^NSEI instead of inventing NIFTY 50. The writer's turn is traced as
agent="query_writer"; the searches stay agent="planner" because they feed
the planner. A writer that is absent or will not answer falls back to the
verbatim question, so the path is strictly additive and cannot fail a run.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add src/econometrica/agents/orchestrator.py tests/agents/test_orchestrator.py
git commit -F /tmp/c3.txt && rm -f /tmp/c3.txt
```

---

## Task 4: Bind the `query_writer` role in the runs router

`_build` constructs the writer only when search is on **and** the role is assigned; a misconfigured writer degrades rather than 503-ing the run.

**Files:**
- Modify: `src/econometrica/api/routers/runs.py` (import + `_build`)
- Test: `tests/api/test_runs.py` (web-search section)

**Interfaces:**
- Consumes: `QueryWriter` (Task 1); `Orchestrator(..., query_writer=...)` (Task 3); the `query_writer` step vocabulary (Task 2).
- Produces: a run whose orchestrator has a `QueryWriter` exactly when `capabilities.web_search` and `"query_writer" in project.model_assignments` and the role is configured.

- [ ] **Step 1: Write the failing API tests**

Add to the web-search section of `tests/api/test_runs.py`:

```python
async def test_a_run_uses_a_model_written_query_when_a_writer_is_assigned(
    client, scripted, monkeypatch
):
    spy = RouteSpyProvider()
    monkeypatch.setattr(
        "econometrica.api.routers.runs.build_search_provider", lambda *a, **k: spy
    )
    # The shared scripted provider now serves three turns in order: the query
    # writer, then the plan, then the narrative.
    scripted.provider.responses = [
        json.dumps({"queries": ["Nifty 50 ticker symbol Yahoo Finance"]}),
        json.dumps(PLAN),
        NARRATIVE,
    ]

    project = (await client.post("/api/projects", json={"name": "Writer"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "validation_tier": "single",
            "web_search_enabled": True,
            "model_assignments": {
                "planner": {"provider": "ollama", "model": "fake-1"},
                "narrator": {"provider": "ollama", "model": "fake-1"},
                "query_writer": {"provider": "ollama", "model": "fake-1"},
            },
        },
    )
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()

    response = await client.post(
        f"/api/chats/{chat['id']}/runs", json={"question": QUESTION}
    )

    assert response.status_code == 200
    assert spy.asked == ["Nifty 50 ticker symbol Yahoo Finance"]
    trace = events(response.text)[-1]["data"]["payload"]["trace"]
    assert any(
        step["agent"] == "query_writer" and step["kind"] == "llm" for step in trace
    )


async def test_a_misconfigured_query_writer_degrades_to_the_verbatim_question(
    client, scripted, monkeypatch
):
    """A search aid must not 503 a run. Unknown provider on the writer role ->
    no writer built -> the verbatim question is searched."""
    spy = RouteSpyProvider()
    monkeypatch.setattr(
        "econometrica.api.routers.runs.build_search_provider", lambda *a, **k: spy
    )
    # No writer turn is consumed, because the writer is never built.
    scripted.provider.responses = [json.dumps(PLAN), NARRATIVE]

    project = (await client.post("/api/projects", json={"name": "Broken"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "validation_tier": "single",
            "web_search_enabled": True,
            "model_assignments": {
                "planner": {"provider": "ollama", "model": "fake-1"},
                "narrator": {"provider": "ollama", "model": "fake-1"},
                "query_writer": {"provider": "nope", "model": "m"},
            },
        },
    )
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()

    response = await client.post(
        f"/api/chats/{chat['id']}/runs", json={"question": QUESTION}
    )

    assert response.status_code == 200
    assert spy.asked == [QUESTION]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_runs.py -k "writer" -q`
Expected: FAIL — the first asserts `spy.asked == ["Nifty 50 ..."]` but gets `[QUESTION]` (the router does not build a writer yet, so the orchestrator falls back to verbatim); the scripted provider will also raise "ran out of scripted responses" because the writer turn is never consumed, leaving `[PLAN, NARRATIVE]` misaligned. Either failure confirms the wiring is absent.

- [ ] **Step 3: Bind the role in `_build`**

In `src/econometrica/api/routers/runs.py`, add the import:

```python
from econometrica.agents.query_writer import QueryWriter
```

In `_build`, after the `searcher` block and before the `coder` block, add:

```python
    # Built only when search is on and the role is assigned. A misconfigured
    # writer must not 503 a run: it is a search aid, and the web-search subsystem
    # degrades rather than fails. Only a core role (planner, narrator) is worth
    # refusing a run over. Mirrors the searcher build, which swallows its own
    # construction errors for the same reason.
    query_writer = None
    if capabilities.web_search and "query_writer" in (project.model_assignments or {}):
        try:
            qw_provider, qw_model = _bind("query_writer", project, registry)
            query_writer = QueryWriter(qw_provider, qw_model)
        except HTTPException:
            query_writer = None
```

Pass it to the `Orchestrator(...)` call (after `web_search=capabilities.web_search,`):

```python
        query_writer=query_writer,
```

- [ ] **Step 4: Run the API tests to verify they pass**

Run: `uv run pytest tests/api/test_runs.py -k "writer or search" -q`
Expected: PASS — including the unchanged `test_a_run_searches_when_the_project_enables_it`, which assigns no writer and so still asserts the verbatim floor `spy.asked == [QUESTION]`.

- [ ] **Step 5: Full runs suite + lint + types**

Run: `uv run pytest tests/api/test_runs.py -q`, `uv run ruff check src tests`, `uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/c4.txt <<'EOF'
feat(runs): bind a query_writer role when search is on

The router builds the query writer only when web search is on and the
query_writer role is assigned, so a project without the role keeps
searching the verbatim question -- the floor. A writer whose provider is
unknown or unconfigured degrades to that floor rather than 503-ing the
run, caught the same way the searcher build is: search is context, and
only planner and narrator are worth refusing a run over. agents/ still
decides nothing about projects; the router does, and passes the writer in.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add src/econometrica/api/routers/runs.py tests/api/test_runs.py
git commit -F /tmp/c4.txt && rm -f /tmp/c4.txt
```

---

## Task 5: Live probe — the motivating case, against reality

The unit tests prove the wiring; only reality proves the query works. This drives a real small chat model to write a query for the NSEI question, searches it against live DuckDuckGo, and asserts `^NSEI` appears — the assertion that the motivating case is actually closed, not merely wired. Skips when Ollama or the network is absent, like the other live tests.

**Files:**
- Create: `tests/agents/test_live_query_writer.py`

**Interfaces:**
- Consumes: `QueryWriter` (Task 1); `OllamaProvider`; `build_search_provider` from `econometrica.tools.web_search`.

- [ ] **Step 1: Write the live probe**

Create `tests/agents/test_live_query_writer.py`:

```python
"""The query writer against a real model and a real search engine.

The unit tests prove the wiring; this proves the query. CLAUDE.md records the
measurement this closes: the verbatim NSEI question surfaced no symbol, while
"Nifty 50 ticker symbol Yahoo Finance" returned ^NSEI. Here a real model writes
the query and a real search runs it, and the assertion is that the symbol comes
back — the thing no unit test can promise.

Skips when Ollama or the network is absent, like every other live test. Read the
report, not just the exit code.
"""

import pytest

from econometrica.agents.query_writer import QueryWriter
from econometrica.llm.providers.ollama import OllamaProvider
from econometrica.tools.web_search import build_search_provider

MODEL = "ministral-3:8b"
NSEI_QUESTION = (
    "How has the National Stock Exchange of India grown over the last 10 years?"
)


def _ollama_is_up() -> bool:
    import httpx

    try:
        httpx.get("http://localhost:11434/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return True


def _ddg_is_up() -> bool:
    import httpx

    try:
        httpx.get("https://lite.duckduckgo.com/", timeout=8.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_a_written_query_surfaces_the_nifty_symbol() -> None:
    if not _ollama_is_up():
        pytest.skip("ollama is not running")
    if not _ddg_is_up():
        pytest.skip("duckduckgo is not reachable")

    writer = QueryWriter(OllamaProvider(), MODEL)
    result = await writer.write(NSEI_QUESTION)
    assert result.output.queries, "the writer produced no query"

    provider = build_search_provider("duckduckgo")
    surfaced = ""
    for query in result.output.queries:
        for hit in await provider.search(query, limit=5):
            surfaced += f"{hit.title} {hit.url} {hit.snippet}\n"

    # The motivating case: the written query, unlike the verbatim question,
    # brings back the symbol the Planner needs.
    assert "NSEI" in surfaced.upper(), (
        "expected a written query to surface ^NSEI; got:\n" + surfaced
    )
```

- [ ] **Step 2: Run it (or watch it skip)**

Run: `uv run pytest tests/agents/test_live_query_writer.py -q -m live`
Expected: PASS if Ollama (`ministral-3:8b`) and the network are up; SKIP otherwise. If it FAILS with a real model and network, that is the finding — capture the `surfaced` text and reconsider the prompt wording before claiming the case closed. Note the CLAUDE.md caveat: a small model may need a prompt nudge, and the assertion is on the *symbol*, not the arithmetic.

- [ ] **Step 3: Commit**

```bash
cat > /tmp/c5.txt <<'EOF'
test(agents): live probe that a written query surfaces ^NSEI

The unit tests prove the wiring; only reality proves the query. A real
model writes a query for the verbatim NSEI question and a real DuckDuckGo
search runs it, asserting the symbol comes back -- the measurement
CLAUDE.md recorded as the motivating case. Skips without Ollama or the
network, like the other live tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add tests/agents/test_live_query_writer.py
git commit -F /tmp/c5.txt && rm -f /tmp/c5.txt
```

---

## Task 6: Record that the gap is closed

CLAUDE.md and README both describe web search as wired-but-ineffective. Update them so the next session does not re-derive the gap.

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Find the paragraph beginning **"The verbatim question is a poor search query, and this is measured, not suspected."** and the one beginning **"So the wiring works and the motivating case is not yet fixed"**. Replace them with a record that the gap is closed by the query writer: the writer turns the question into up to three symbol-shaped queries before planning; it is a dedicated `query_writer` model role that degrades to the verbatim question when absent, misconfigured, or unable to answer; its billed turn is traced as `agent="query_writer"` (which cost a hand-written CHECK migration, the `quant_coder` trap again); and the searches stay `agent="planner"` because they feed the planner. Keep the measured table — it is why the writer exists — but reframe it as the motivation the writer now acts on rather than an open gap. Note the live probe `tests/agents/test_live_query_writer.py` as the proof against reality.

Also update the **"Web search reads the *resolved* capability"** paragraph if needed to mention the writer role, and add `query_writer` beside `validator`/`quant_coder` wherever the optional model roles are enumerated.

- [ ] **Step 2: Update README.md**

Update the capability-map banner / web-search line so the public front door reflects that search now writes a symbol-shaped query before planning, and that `query_writer` is an optional model role. Keep it to the level of detail the rest of the README uses. Do not commit any key material (the repo is public).

- [ ] **Step 3: Commit**

```bash
cat > /tmp/c6.txt <<'EOF'
docs: record the web-search gap is closed by the query writer

CLAUDE.md and the README described web search as wired but ineffective:
the verbatim question surfaced no ticker. The query writer closes it -- a
dedicated query_writer role turns the question into symbol-shaped lookups
before planning, degrading to the verbatim question when absent or
misconfigured. Records the dedicated role, the agent="query_writer" trace
turn and its CHECK migration, and the live probe that proves ^NSEI now
comes back, so the next session does not re-derive the gap.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add CLAUDE.md README.md
git commit -F /tmp/c6.txt && rm -f /tmp/c6.txt
```

---

## Final verification

After all tasks, from `backend/` (Postgres up):

- [ ] `uv run pytest -q` — full backend suite green (1431 + the new tests; 1 pre-existing skip). The live probe skips without Ollama.
- [ ] `uv run ruff check src tests alembic` — clean.
- [ ] `uv run mypy src` — clean.
- [ ] `uv run alembic upgrade head && uv run alembic check` — applies `f0a1c2d3e4b5`, no drift.
- [ ] Optionally, with Ollama up: `uv run pytest -m live tests/agents/test_live_query_writer.py -q` — the motivating case, closed against reality.

---

## Self-review notes

- **Spec coverage.** New `QueryWriter` + `SearchQuery` (Task 1) ↔ design "Components / the new agent". `_search_context` rewrite, `MAX_SEARCH_QUERIES=3`, sequential search, verbatim fallback, writer turn `agent="query_writer"` / search steps `agent="planner"` (Task 3) ↔ design "the new flow" and "search steps stay agent=planner". Router binding + degradation (Task 4) ↔ design "binding the role". Migration + two DB gates (Task 2) ↔ design "the migration". Live probe (Task 5) ↔ design "testing / live probe". Docs (Task 6) ↔ closing the CLAUDE.md open item. Narrator-still-blind and grounding-gate-unchanged are asserted by omission (nothing routes results to the Narrator) and by the existing web_search grounding test, which this change does not touch.
- **Type consistency.** `SearchQuery.queries: list[str]`; `QueryWriter.write(question) -> AgentResult[SearchQuery]`; `Orchestrator.__init__(..., query_writer: QueryWriter | None = None)`; `_search_queries(question, trace) -> list[str]`. `add_agent_turn(agent="query_writer", ...)` matches the widened `STEP_AGENTS`. `_bind` returns `(LLMProvider, str)`, consumed as `qw_provider, qw_model`.
- **No placeholders.** Every code and test block is complete; the only prose-only step is Task 6's doc edits, which are text changes to existing files and name the exact paragraphs.
