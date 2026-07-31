# Econometrica — a model-written search query before planning

*Design note, 2026-07-31. Read `CLAUDE.md` first; this closes the open item it
records under "Web search runs, as of 2026-07-30" — search is wired and does
**not** yet fix what it was built for.*

---

## The problem, measured

`Orchestrator._search_context` searches the user's question **verbatim** and
appends the attributed results to the Planner's context. The motivation was
real: a Planner invented `LON` for London real estate and `NSEI` for the Nifty
50 (the listed symbol is `^NSEI`), and both runs died in the Data Steward
rather than in the model — a model asked to name a listed instrument with
nothing in front of it.

But the verbatim question is a poor search query, and this was probed against
live DuckDuckGo on 2026-07-30, not suspected:

| query | symbols surfaced |
|---|---|
| "How has the National Stock Exchange of India grown over the last 10 years?" | none |
| "Nifty 50 Yahoo Finance ticker symbol" | **`^NSEI`** — top hit is "NIFTY 50 (^NSEI) - Yahoo Finance" |
| "How has London's real estate moved over the last 30 years?" | none |

An analytical question returns market commentary; a symbol-shaped query returns
the symbol. Re-running the NSEI question with search on, the Planner invented
`NIFTY 50` again and the run died in the Data Steward exactly as before.

**The fix cannot be deterministic.** The winning query was `"Nifty 50 Yahoo
Finance ticker symbol"` — that required pulling the instrument name *"Nifty
50"* out of the prose *"National Stock Exchange of India"*. A string transform
cannot do that extraction; a model can. So one model turn before planning is
genuinely load-bearing here, which is why it was deferred rather than assumed:
it is a billed turn and a parse that can fail.

---

## The shape

Add a **Query Writer**: a small agent that reads the question and emits a short
list of symbol-shaped search queries. `_search_context` searches those instead
of the verbatim question, and the attributed results reach the Planner exactly
as they do today. Everything else about web search is unchanged — most
importantly, the **Narrator still never sees the results**, because the
grounding gate judges its output and web snippets are dense with numbers it
could not match. That is a mechanism, not caution, and this change does not
touch it.

Three independent conditions gate the path, and their combination decides what
happens:

- `web_search` — the resolved capability (project setting a chat may override).
- `searcher` — a search *provider*, present only when one is configured on this
  deployment.
- `query_writer` — a **model role**, assigned in `Project.model_assignments`
  like `validator` and `quant_coder`.

The first two already gate whether any search runs at all. The third is new and
decides only *how the query is written*:

| `web_search` and `searcher`? | `query_writer` assigned? | behaviour |
|---|---|---|
| no | — | no search — context unchanged (**as today**) |
| yes | no | search the verbatim question (**today's floor, preserved**) |
| yes | yes | search the model-written queries |

The floor matters: with search on but no query writer configured, the run
behaves exactly as it does now. The feature is strictly additive and can never
do worse than the wiring it replaces.

---

## Components

### `agents/query_writer.py` — the new agent

A `QueryWriter(Agent[SearchQuery])`, role `query_writer`, built like every other
agent: temperature 0, JSON mode, the shared two-attempt retry loop in
`agents/base.py`. It knows nothing about projects — it takes a provider, a
model, and a question.

Its system prompt teaches one job: turn a finance question into short web
queries that will surface the exact ticker/symbol for each instrument the
question names, in the form a market data vendor uses (`^NSEI`, `BTC-USD`). The
measured winning pattern — the instrument's common name plus words like *ticker
symbol* and *Yahoo Finance* — is the template it is told to follow, because the
real market source is yfinance and yfinance-shaped symbols are what the Data
Steward can actually resolve.

Output contract:

```json
{ "queries": ["Nifty 50 ticker symbol Yahoo Finance"] }
```

`SearchQuery` is a Pydantic model local to this module (not `agents/schemas.py`
— it is consumed immediately inside `_search_context` and never passed
downstream, so it is not a cross-agent contract). Its validator strips each
query, drops empties, and de-duplicates case-insensitively; the model requires
at least one query to survive. `check` rejects an empty list so a useless reply
spends a retry rather than the run. The **cap to three** is applied at the call
site as a named policy, not baked into the schema — the schema records what the
model said; the orchestrator decides how many to act on.

Why three: a question naming two instruments (`"AAPL against the Nifty 50"`)
needs two lookups, and a small headroom is cheap. Above that is a runaway model,
not a real question, and each query is a live network call against a keyless,
contract-free endpoint.

### `agents/orchestrator.py` — `_search_context`, rewritten

The orchestrator gains one constructor parameter, `query_writer: QueryWriter |
None = None`, passed in for the same reason `searcher` and `code_sandbox` are:
`agents/` decides nothing about projects, and the router supplies a writer only
when the role is assigned and search is on.

The new flow:

1. If not (`self.web_search and self.searcher is not None`): return the context
   unchanged. No search runs. (Unchanged.)
2. Build the query list:
   - `query_writer` present → call it. Record its LLM turn on the trace as
     `agent="query_writer"`. Take the first three of `result.queries`.
   - On the writer refusing or exhausting its attempts → fall back to
     `[question]`. Search is context; losing it — or the whole run — to a query
     writer that would not answer is the worse trade.
   - `query_writer` absent → `[question]`.
3. For each query, `await search(query, provider=self.searcher,
   enabled=self.web_search)`, **sequentially**. Sequential rather than gathered
   because the keyless DuckDuckGo provider "reads an HTML page with no API
   contract behind it" (CLAUDE.md) and concurrent hammering is how a fragile
   endpoint starts refusing. Record each as a `web_search:<provider>` tool step.
4. Concatenate the non-empty `as_context()` blocks and append them to the
   Planner's context, exactly as one block is appended today.

**Search steps stay `agent="planner"`.** Only the query-writing LLM turn is
`agent="query_writer"`. The distinction is honest: the search *feeds* the
planner — that is what its step has recorded since the feature shipped, and
`test_the_search_step_records_what_the_planner_was_shown` asserts it — while the
query writer's own billed contribution is the one model call that wrote the
queries. Filing the searches under the writer would move no cost (tool steps
carry no tokens) and would only make the trace read as if the searches were the
writer's product rather than the planner's input.

### `api/routers/runs.py` — binding the role

`_build` constructs the writer only when both conditions hold:

```python
query_writer = None
if capabilities.web_search and "query_writer" in (project.model_assignments or {}):
    try:
        provider, model = _bind("query_writer", project, registry)
        query_writer = QueryWriter(provider, model)
    except HTTPException:
        # A misconfigured query writer must not 503 a run. Search is context and
        # degrades to the verbatim question; only a core role (planner, narrator)
        # is worth refusing a run over.
        query_writer = None
```

The `try/except` is the one wrinkle. `_bind` raises `HTTPException` for an
unknown provider or a missing key, and for `validator` and `quant_coder` that
correctly 503s the run — those are roles a run cannot sensibly proceed without
once asked for. The query writer is different: it is a search aid, and the whole
web-search subsystem is built to degrade rather than fail. So its bind failure
is caught and folded into "no writer configured", which the orchestrator already
handles as the verbatim floor. This mirrors the existing `searcher` build, which
swallows `KeyError`/`ValueError` for exactly the same reason.

---

## The migration

The query writer's billed LLM turn is recorded with `agent="query_writer"`, and
that value has to be allowed to land in `run_steps`. Two vocabularies name it:

- `db/models/run.py:STEP_AGENTS` — the tuple behind the
  `ck_run_steps_agent_known` CHECK constraint. **This is the enforced one.**
- `agents/trace.py:AGENTS` — documentation of the roles a trace may carry;
  updated for parity, though nothing validates against it.

Adding to `STEP_AGENTS` alone is the trap CLAUDE.md records from `quant_coder`:
autogenerate emits a CHECK when it *creates* a table and sees nothing when one
changes on a table that already exists, so the revision comes out empty and a
fresh database rejects every query-writer step while the unit suite — built with
`create_all`, not the migrations — stays green. So a **hand-written revision**
widens the constraint, drop-and-recreate (Postgres has no `ALTER CONSTRAINT` for
a CHECK), with the downgrade deleting `query_writer` rows first so narrowing does
not fail over rows that already violate it. The template is
`b3a17c0d9e42_quant_coder_step_agent.py`, verbatim in structure.

`tests/db/test_migrations.py` asserts every *value* of each vocabulary reaches
some migration, and `tests/db/test_run_model.py` inserts a step of the new agent
against the real database. Together they are the whole gate — `alembic check`
sees neither the widened CHECK nor the missing one.

---

## Failure contract

Nothing in this change can fail a run. In spirit it is unchanged; enumerated:

- **Query writer refuses or exhausts its attempts** → fall back to the verbatim
  question. The run proceeds with today's floor.
- **Query writer misconfigured** (unknown provider, no key) → no writer built;
  verbatim floor.
- **A search fails** (endpoint down, parse empty) → that query contributes
  nothing; the others still do; the run completes. (Unchanged — `search`
  already returns a failed `SearchOutcome` rather than raising.)
- **Every search fails** → context unchanged, Planner runs without web context,
  as it does with search off.

The only new billed cost is the one query-writer turn, and it buys the
difference between a Planner shown `^NSEI` and a Planner inventing `NIFTY 50`.

---

## Testing, red first

Strict TDD — each test written, run, and watched fail with the expected error
before the implementation exists.

**`tests/agents/test_query_writer.py`** — the agent in isolation, against a
`FakeProvider`:

- a question about a named index yields a symbol-shaped query for it;
- an empty `queries` list is rejected and spends a retry;
- whitespace-only and duplicate queries are stripped and de-duplicated;
- a question naming two instruments yields (up to) two queries.

**`tests/agents/test_orchestrator.py`** — the four existing web-search tests
change, because they encode the verbatim behaviour this replaces. With a
`query_writer` wired (the planner fake now also serves the writer's turn, or a
separate writer provider is passed):

- the search provider is asked the **generated** query, not the verbatim
  question;
- at most three searches run for a writer that emits more;
- a writer that refuses falls back to searching the verbatim question;
- the trace carries the `query_writer` LLM turn, and it precedes both the search
  steps and the planner's turn;
- with **no** writer configured, the verbatim question is still searched (the
  floor).

**`tests/db/test_run_model.py` / `test_migrations.py`** — a `query_writer` step
inserts against Postgres; the value reaches a migration.

**Live probe, `@pytest.mark.live`** — drives a real small chat model to write a
query for the NSEI question, searches it against live DuckDuckGo, and asserts
`^NSEI` appears in the results. Skips when Ollama or the network is absent, like
the other live tests. This is the assertion that the motivating case is actually
closed — the unit tests prove the wiring; only reality proves the query works.

---

## What this does not do

- It does not give the Narrator web results. That needs a separate design that
  answers the grounding-gate problem first.
- It does not change the search providers, the caching, or the attribution
  format.
- It does not make the query writer mandatory. A deployment that wants search
  without the extra turn simply leaves the role unassigned.
