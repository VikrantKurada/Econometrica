# Web search in a run: design note

**Date:** 2026-07-30
**Status:** approved, not implemented

First of three. `tools/web_search.py`, `services/rag.py` and `mcp/` are each
built, tested, and imported by nothing but their own tests. This note wires the
first one; retrieval and MCP get their own, because they are not the same size
and not the same shape.

---

## What is already there

`tools/web_search.py` is not a stub. It has two providers behind a
`SearchProvider` protocol and a `SEARCH_PROVIDERS` registry, in the shape
`llm/registry.py` and `data/registry.py` established. `search()` returns a
`SearchOutcome` carrying:

- `as_context()` — the results as prompt text, each attributed, under a header
  that says *read from the web, not computed. Nothing here is a result.*
- `to_step_record()` — already stamped `agent="planner"`, `kind="tool"`,
  `tool="web_search:<provider>"`.

Both of those are decisions the author already made. The remaining work is the
call site, and this note is mostly about not undoing them.

---

## Where it runs

In `Orchestrator._pipeline`, immediately before `self._plan(...)`. The Planner
is the agent that benefits: it chooses tickers, a window and a frequency from
prose, and **the failures in this project's own history are exactly that** — a
Planner invented the ticker `LON` for London real estate and `NSEI` for the
Nifty 50, and both runs died in the Data Steward with `DataUnavailableError`.
Neither is a reasoning failure. They are missing-context failures.

The query is the user's question, verbatim. No extra model call, nothing to
parse, nothing to retry. It searches poorly for a long analytical question and
that is an accepted cost: the alternative is a billed turn before planning, and
the value here has not been demonstrated yet.

### Three ways it does not run, all traced rather than silent

1. **Capability off.** `search()` *raises* `SearchDisabledError` when asked with
   the capability off — deliberately, because asking anyway is a programming
   error and an empty result would hide it. So the orchestrator must gate
   before the call, never catch after. A disabled search never reaches a
   provider.
2. **No provider configured.** Skip.
3. **The provider fails.** `search()` already returns
   `SearchOutcome(failed=True, detail=...)` rather than raising. Record the
   step, continue the run. Losing an analysis to a search outage is the worse
   trade — search is context, and the run's numbers do not depend on it.

The keyless DuckDuckGo provider scrapes an HTML page with no API contract, so
(3) is not hypothetical. It has a live test for that reason, and its markup
uses **single** quotes (`class='result-link'`), which is how the parser was
wrong the first time.

---

## How it reaches the Orchestrator

`Orchestrator.__init__` gains two parameters:

```python
searcher: SearchProvider | None = None,
web_search: bool = False,
```

which is exactly the `coder` / `code_sandbox` pair already there, for exactly
the same reason: `agents/` knows nothing about projects or chats, and
`services.capabilities` is the one place that decides. `api/routers/runs.py::_build`
already resolves capabilities for the sandbox; it builds the provider in the
same block.

The orchestrator searches only when `self.web_search and self.searcher is not
None`. `search()` then checks the flag again and raises if it is false — a
belt-and-braces guard that is dead at this call site by construction and alive
for every other caller. It stays.

Provider selection is an env setting, `ECONOMETRICA_SEARCH_PROVIDER`, defaulting
to `duckduckgo` because it needs no key — the same shape as
`ECONOMETRICA_PRICE_SOURCE`, and `build_search_provider` already exists to
consume it. Brave's key is a settings field, `BRAVE_API_KEY`, beside
`NVIDIA_API_KEY` and the rest. **Not** the keystore: that is reached through
`PUT /api/providers/{name}/key`, whose name is validated against the LLM
provider registry, so there would be no way to put a search key in it without
teaching that route about a second kind of provider.

### One narrowing, on the way past

`search()` currently takes `capabilities: ResolvedCapabilities` and reads one
boolean off it. That makes `tools/` — a generic layer — import a `services/`
type that in turn imports `db.models`. The parameter narrows to
`enabled: bool`. It is a two-line change to code this task is already touching,
and it stops the import chain `agents/ -> tools/ -> services/ -> db.models`
existing for one flag.

---

## Where the text goes, and where it deliberately does not

`as_context()` is appended to the `context` string already passed to
`Planner.plan(question, context=...)`, under its own header, after any context
the user typed into `RunStart.context`.

**It does not go to the Narrator, and that is a mechanism rather than caution.**
The Narrator is the one agent whose output passes the grounding gate, and the
gate blocks any number that is not in a `ResultSet`. Web snippets are dense with
numbers. Feed them to the Narrator and it will eventually paraphrase one, and
the gate will withhold the **entire narration** — leaving the user with no
interpretation at all, which is worse than an uninformed one. If the Narrator
should ever have this, it needs a design that answers that first.

---

## What the trace records

`to_step_record()` supplies agent, kind, status and tool. Two fields are filled
in beyond it:

- `prompt` — the query
- `response` — the snippets that were handed to the Planner

Both truncated at `agents.base.PROMPT_LIMIT`. These are the fields 6.10 added
to answer "what was the model actually shown", and using them means the Trace
tab renders the search with no new UI: a reader can see what influenced the
plan, not merely that a search happened.

The step is a root-level entry immediately preceding the planner step.

---

## What must not change

`tools/` is context, `econ/` is computation. The grounding gate admits only what
a registry tool computed, and there is already a test proving a figure quoted
verbatim out of search text stays blocked. **That test does not move.** This
task adds a second one at a different level: a full run with search context in
the Planner's prompt still cannot launder a number into a published narration.

---

## Tests, written first

- capability off: the provider is **never called** (assert on a spy, not on the
  outcome — the outcome would look the same if it were called and ignored)
- no provider configured: the run plans normally, no search step
- provider raises: the run completes, the trace carries a `failed` search step,
  and the Planner's context contains no search header
- success: the header is present in the Planner's prompt, and the step's
  `prompt` and `response` carry the query and the snippets
- the step precedes the planner step in the trace
- grounding: a run with search context cannot publish a number that came from a
  snippet
- the existing `@pytest.mark.live` DuckDuckGo test stays, unchanged, as the
  canary for the scraper's markup

---

## What this deliberately does not do

- **No search for the Narrator or the Validator.** See above.
- **No model-written query.** Revisit when there is evidence the verbatim
  question is the thing limiting usefulness.
- **No per-project provider choice.** An env setting matches how every other
  source in this project is selected.
- **No caching of search results.** The price cache exists because a stale
  price is a *different series*; a stale snippet is just an old snippet, and a
  re-run does not re-search — it does not re-plan at all.
- **Nothing about retrieval or MCP.** Separate notes.

---

## Size

`agents/orchestrator.py`, `api/routers/runs.py`, `config.py` and
`tools/web_search.py` touched. One settings key, no migration, no new module,
about eight tests.
