# Web search in a run: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A run with web search enabled searches the user's question before
planning, and the results reach the Planner as attributed context.

**Architecture:** The orchestrator gains `searcher` / `web_search`, mirroring the
`coder` / `code_sandbox` pair already there — `agents/` learns nothing about
projects or chats, and `api/routers/runs.py::_build` decides. The outcome joins
the Planner's context string and becomes a root-level trace step.

**Tech Stack:** Python 3.12, FastAPI, httpx, pytest.

**Design note:** `docs/plans/2026-07-30-web-search-wiring-design.md`.

## Global Constraints

- Everything runs under `uv run` from `backend/`. No venv to activate.
- **TDD, strictly.** Write the test, run it, watch it fail with the expected
  error, then implement.
- `uv run ruff check src tests alembic` and `uv run mypy src` stay clean.
- Conventional Commits, reasoning in the body. Write the message to a file and
  use `git commit -F` — PowerShell mangles `-m` when the message has quotes.
- `docker compose up -d db --wait` before running the suite.
- **`tools/` is context, `econ/` is computation.** Nothing here may let a
  searched number become a reported one. The existing grounding test does not
  move.
- No migration. Nothing here changes a table.

---

### Task 1: Narrow `search()` to a plain flag

`search()` takes `capabilities: ResolvedCapabilities` and reads one boolean off
it, which makes `tools/` import `services/`, which imports `db.models`. The
orchestrator will call this, so the chain would become
`agents/ -> tools/ -> services/ -> db.models` for one flag.

**Files:**
- Modify: `backend/src/econometrica/tools/web_search.py:103-131`
- Test: `backend/tests/tools/test_web_search.py`

**Interfaces:**
- Produces: `async search(query: str, *, provider: SearchProvider, enabled: bool, limit: int = DEFAULT_LIMIT) -> SearchOutcome`

- [ ] **Step 1: Update the existing call sites in the test file**

In `backend/tests/tools/test_web_search.py`, every call currently passing
`capabilities=...` becomes `enabled=...`. A call that passed
`ResolvedCapabilities(web_search=True, ...)` becomes `enabled=True`; one that
passed `web_search=False` becomes `enabled=False`. Delete the now-unused
`ResolvedCapabilities` import if nothing else in the file uses it.

- [ ] **Step 2: Run the tests and watch them fail**

```bash
cd backend && uv run pytest tests/tools/test_web_search.py -q
```

Expected: `TypeError: search() got an unexpected keyword argument 'enabled'`.

- [ ] **Step 3: Change the signature**

```python
async def search(
    query: str,
    *,
    provider: SearchProvider,
    enabled: bool,
    limit: int = DEFAULT_LIMIT,
) -> SearchOutcome:
    """Search the web, if this chat is allowed to.

    Takes the resolved flag rather than the whole `ResolvedCapabilities`: this
    layer is generic, and importing a `services/` type to read one boolean puts
    `db.models` on the import path of every module that touches a search. Who
    resolved the flag, and from which project and chat, is decided in
    `api/routers/runs.py` where those things are known.
    """
    if not enabled:
        # Raised rather than returned empty: asking with the capability off is a
        # programming error, and an empty result would hide it.
        raise SearchDisabledError(
            "web search is off for this chat. It is a per-project setting that a"
            " chat inherits and may override, and it is off by default."
        )
```

Leave the body below unchanged. Remove the now-unused
`from econometrica.services.capabilities import ResolvedCapabilities` import.

- [ ] **Step 4: Run the tests**

```bash
cd backend && uv run pytest tests/tools/test_web_search.py -q && uv run ruff check src tests && uv run mypy src
```

Expected: all pass, clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/econometrica/tools/web_search.py backend/tests/tools/test_web_search.py
git commit -F <message file>
```

Subject: `refactor(tools): take the resolved search flag, not the capabilities`.
Body: the import chain, and that the resolution stays where projects are known.

---

### Task 2: The orchestrator searches before planning

**Files:**
- Modify: `backend/src/econometrica/agents/orchestrator.py` — `__init__` (~line 92) and `_pipeline` (~line 152)
- Test: `backend/tests/agents/test_orchestrator.py`

**Interfaces:**
- Consumes: `search(...)` from Task 1; `SearchOutcome`, `SearchProvider` from `econometrica.tools.web_search`.
- Produces: `Orchestrator(..., searcher: SearchProvider | None = None, web_search: bool = False)`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agents/test_orchestrator.py`, following the fake-agent
pattern already in that file:

```python
class SpyProvider:
    """Records that it was asked, which is the assertion for the off case."""

    name = "spy"

    def __init__(self, results=None, boom: str = "") -> None:
        self.results = results or []
        self.boom = boom
        self.asked: list[str] = []

    async def search(self, query: str, *, limit: int = 5):
        self.asked.append(query)
        if self.boom:
            raise RuntimeError(self.boom)
        return self.results


def a_result():
    from econometrica.tools.web_search import SearchResult

    return SearchResult(
        title="Nifty 50 index",
        url="https://example.invalid/nifty",
        snippet="The Nifty 50 trades under the symbol ^NSEI.",
    )


def search_step(outcome):
    return next(s for s in outcome.trace if (s.tool or "").startswith("web_search:"))


async def test_search_context_reaches_the_planner():
    provider = SpyProvider([a_result()])
    orchestrator, fakes = build(searcher=provider, web_search=True)

    await orchestrator.run(QUESTION)

    assert provider.asked == [QUESTION]
    # `FakeProvider.calls` records the messages each turn was sent; the first
    # turn is the Planner's, and the search text rides in its user message.
    planner_prompt = str(fakes["planner"].calls[0])
    # The header is what tells the model this is read, not computed.
    assert "read from the web, not computed" in planner_prompt
    assert "^NSEI" in planner_prompt


async def test_a_disabled_search_never_reaches_the_provider():
    """Asserted on the spy, not on the outcome.

    An outcome-level assertion would pass just as well if the provider had been
    called and its results dropped, which is the bug worth preventing.
    """
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(searcher=provider, web_search=False)

    await orchestrator.run(QUESTION)

    assert provider.asked == []


async def test_a_failed_search_degrades_the_run_rather_than_failing_it():
    provider = SpyProvider(boom="the endpoint returned 503")
    orchestrator, fakes = build(searcher=provider, web_search=True)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert "read from the web" not in str(fakes["planner"].calls[0])
    step = search_step(outcome)
    assert step.status == "failed"
    assert "503" in step.detail


async def test_the_search_step_records_what_the_planner_was_shown():
    provider = SpyProvider([a_result()])
    orchestrator, _ = build(searcher=provider, web_search=True)

    outcome = await orchestrator.run(QUESTION)

    step = search_step(outcome)
    assert step.kind == "tool"
    assert step.agent == "planner"
    assert step.prompt == QUESTION
    assert "^NSEI" in step.response
    # It informed the plan, so it has to come before the plan in the trace.
    assert outcome.trace.index(step) < next(
        i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm"
    )
```

`build(...)` is the helper already at `tests/agents/test_orchestrator.py:80`.
It does not take these parameters yet, so extend its signature with
`searcher: object | None = None, web_search: bool = False` and forward both to
the `Orchestrator(...)` it constructs. `QUESTION` and `FakeProvider` are already
imported in that module.

If `FakeProvider` does not expose the messages it was sent as `.calls`, check
what it does expose — `tests/api/test_runs.py` asserts on
`scripted.provider.calls`, so the attribute exists; only its element shape needs
confirming before writing the two `str(...)` assertions above.

- [ ] **Step 2: Run them and watch them fail**

```bash
cd backend && uv run pytest tests/agents/test_orchestrator.py -k "search" -v
```

Expected: `TypeError: Orchestrator.__init__() got an unexpected keyword argument 'searcher'`.

- [ ] **Step 3: Add the constructor parameters**

In `Orchestrator.__init__`, after `code_sandbox`:

```python
        searcher: SearchProvider | None = None,
        web_search: bool = False,
```

and in the body, beside the `code_sandbox` assignment:

```python
        #: Passed in for the same reason `code_sandbox` is: `agents/` knows
        #: nothing about projects or chats, and `services.capabilities` is the
        #: one place that decides. Both are needed — a provider may be absent
        #: because none is configured, which is not the same as being off.
        self.searcher = searcher
        self.web_search = web_search
```

Import at the top:

```python
from econometrica.tools.web_search import SearchProvider, search
```

- [ ] **Step 4: Search before planning**

In `_pipeline`, replace the line `plan = await self._plan(question, context, outcome, trace)`
with:

```python
        context = await self._search_context(question, context, trace)
        plan = await self._plan(question, context, outcome, trace)
```

and add the method:

```python
    async def _search_context(self, question: str, context: str, trace: TraceBuilder) -> str:
        """Web results as extra context for the Planner, or the context unchanged.

        The Planner is the agent that benefits: it picks tickers and a window
        out of prose, and the failures this exists to reduce are exactly that —
        a Planner invented `LON` for London real estate and `NSEI` for the Nifty
        50, and both runs died in the Data Steward rather than in the model.

        Deliberately not offered to the Narrator. The Narrator's output is what
        the grounding gate judges, the gate withholds a whole narration over one
        number it cannot match, and web snippets are dense with numbers.
        """
        if not (self.web_search and self.searcher is not None):
            return context

        outcome = await search(question, provider=self.searcher, enabled=self.web_search)

        record = outcome.to_step_record()
        # The fields 6.10 added for "what was the model actually shown". Without
        # them the trace says a search happened but not what it found, which is
        # most of what a reader wants to know about a step that shaped the plan.
        record.prompt = question[:PROMPT_LIMIT]
        record.response = outcome.as_context()[:PROMPT_LIMIT]
        trace.add(record)

        found = outcome.as_context()
        if not found:
            # A failed or empty search leaves the context untouched rather than
            # appending an empty header, which would tell the model a search
            # succeeded and found nothing.
            return context
        return f"{context}\n\n{found}" if context else found
```

Import `PROMPT_LIMIT` from `econometrica.agents.base` if it is not already
imported in this module.

- [ ] **Step 5: Run the tests**

```bash
cd backend && uv run pytest tests/agents/test_orchestrator.py -q
```

Expected: all pass, including the pre-existing ones — every current caller
omits both new parameters and gets the old behaviour.

- [ ] **Step 6: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/econometrica/agents/orchestrator.py backend/tests/agents/test_orchestrator.py
git commit -F <message file>
```

Subject: `feat(agents): search the question before planning`.
Body: why the Planner and not the Narrator, why two parameters rather than one,
and the `LON`/`NSEI` failures that motivated it.

---

### Task 3: The route builds the provider, and the settings name it

**Files:**
- Modify: `backend/src/econometrica/config.py:48-58`
- Modify: `backend/src/econometrica/api/routers/runs.py` — `_build`
- Modify: `.env.example`
- Test: `backend/tests/api/test_runs.py`

**Interfaces:**
- Consumes: `Orchestrator(..., searcher=..., web_search=...)` from Task 2; `build_search_provider(name, *, api_key="")` from `econometrica.tools.web_search`.
- Produces: `Settings.search_provider: Literal["duckduckgo", "brave"]`, `Settings.brave_api_key: str`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/api/test_runs.py`:

```python
class RouteSpyProvider:
    name = "spy"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def search(self, query: str, *, limit: int = 5):
        from econometrica.tools.web_search import SearchResult

        self.asked.append(query)
        return [
            SearchResult(
                title="Nifty 50",
                url="https://example.invalid/nifty",
                snippet="Listed as ^NSEI.",
            )
        ]


async def test_a_run_searches_when_the_project_enables_it(client, scripted, monkeypatch):
    spy = RouteSpyProvider()
    monkeypatch.setattr(
        "econometrica.api.routers.runs.build_search_provider", lambda *a, **k: spy
    )

    project = (await client.post("/api/projects", json={"name": "Search"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "validation_tier": "single",
            "web_search_enabled": True,
            "model_assignments": {
                "planner": {"provider": "ollama", "model": "fake-1"},
                "narrator": {"provider": "ollama", "model": "fake-1"},
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
    steps = events(response.text)[-1]["data"]["payload"]["trace"]
    assert any((s["tool"] or "").startswith("web_search:") for s in steps)


async def test_a_run_does_not_search_when_the_project_does_not_enable_it(
    client, scripted, monkeypatch
):
    """Off by default, and the provider is never even constructed."""
    built = []
    monkeypatch.setattr(
        "econometrica.api.routers.runs.build_search_provider",
        lambda *a, **k: built.append(a) or RouteSpyProvider(),
    )

    chat_id = await make_chat(client)  # make_chat does not enable web search
    await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    assert built == []
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd backend && uv run pytest tests/api/test_runs.py -k "searches or does_not_search" -v
```

Expected: the first fails with `AttributeError: <module 'econometrica.api.routers.runs'>
has no attribute 'build_search_provider'`.

- [ ] **Step 3: Add the settings**

In `backend/src/econometrica/config.py`, after `price_source`:

```python
    #: Which web-search provider a run uses when the capability is on. An env
    #: setting rather than a project field for the same reason
    #: `price_source` is: which vendor this machine can reach is a property of
    #: the deployment, not of a piece of analysis. `duckduckgo` is the default
    #: because it needs no key, which keeps the zero-configuration path intact.
    search_provider: Literal["duckduckgo", "brave"] = Field(
        default="duckduckgo",
        validation_alias=AliasChoices("ECONOMETRICA_SEARCH_PROVIDER", "SEARCH_PROVIDER"),
    )
```

and beside the other vendor keys:

```python
    brave_api_key: str = ""
```

Not the keystore: that is reached through `PUT /api/providers/{name}/key`,
whose name is validated against the LLM provider registry.

- [ ] **Step 4: Build the provider in the route**

In `backend/src/econometrica/api/routers/runs.py`, import:

```python
from econometrica.tools.web_search import build_search_provider
```

and in `_build`, beside the sandbox block:

```python
    # Built only when the capability is on, so a project that has search off
    # never constructs a provider and never reaches a vendor.
    searcher = None
    if capabilities.web_search:
        settings = get_settings()
        try:
            searcher = build_search_provider(
                settings.search_provider, api_key=settings.brave_api_key
            )
        except (KeyError, ValueError):
            # A provider named wrongly, or one needing a key it has not been
            # given, is a deployment problem — and search is context, so it
            # must not cost the user their analysis. The run proceeds with no
            # searcher, which the orchestrator treats as "none configured".
            searcher = None
```

Pass both through to the `Orchestrator(...)` call:

```python
        searcher=searcher,
        web_search=capabilities.web_search,
```

Import `get_settings` from `econometrica.config` if it is not already imported.

- [ ] **Step 5: Document the settings**

Add to `.env.example`, under the application block:

```
# Which provider a run uses when web search is enabled for a project or chat.
#   duckduckgo  no API key, scrapes the HTML results page. The default.
#   brave       needs BRAVE_API_KEY.
# Search is context, never a source of numbers: nothing it returns can become
# a figure in a result or a narration.
ECONOMETRICA_SEARCH_PROVIDER=duckduckgo
BRAVE_API_KEY=
```

- [ ] **Step 6: Run the tests**

```bash
cd backend && uv run pytest tests/api/test_runs.py -q
```

- [ ] **Step 7: Prove the gate still holds**

Add to `backend/tests/agents/test_grounding.py`:

```python
def test_a_number_read_from_a_search_snippet_is_still_ungrounded():
    """`tools/` is context, `econ/` is computation.

    Wiring search into the pipeline is exactly the change that could quietly
    widen what counts as grounded, so the rule gets an assertion that names
    the new source of stray numbers rather than only the old ones.
    """
    # 1.2977 is in ALLOWED because a tool computed it; 18.4 came off a web page.
    report = check_grounding(
        "The beta is 1.2977, and the index rose 18.4% last year.", ALLOWED
    )

    assert report.grounded is False
    assert "18.4" in str(report.ungrounded)
```

`ALLOWED` and `check_grounding` are already imported at the top of that file;
`grounded()` is its one-line wrapper. Confirm the field name on the report — the
existing `test_the_report_names_the_number_and_its_sentence` shows what it is
called — and use that rather than guessing `ungrounded`.

- [ ] **Step 8: Full gate, then commit**

```bash
cd backend && uv run pytest -q && uv run ruff check src tests alembic && uv run mypy src
```

```bash
git add backend/src/econometrica/config.py backend/src/econometrica/api/routers/runs.py backend/tests/api/test_runs.py backend/tests/agents/test_grounding.py .env.example
git commit -F <message file>
```

Subject: `feat(runs): search the web when a project enables it`.
Body: why the provider is an env setting, why a misconfigured provider degrades
rather than refuses, and that the grounding gate is asserted at pipeline level.

---

### Task 4: Documentation

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/assets/build_capability_map.py`

- [ ] **Step 1: Update `CLAUDE.md`**

The existing paragraph beginning "**Web search reads the *resolved*
capability**" describes behaviour that was true of the module and unreachable
from a run. Say that it now runs before planning on the question verbatim, that
its text reaches the Planner and deliberately not the Narrator (with the
withheld-narration reason), that the provider is `ECONOMETRICA_SEARCH_PROVIDER`,
and that a failed search degrades the run.

Also correct the "not wired" list: retrieval and MCP remain, search does not.

- [ ] **Step 2: Update `README.md`**

In the status block, say a run can search the web for context when a project
enables it, and that nothing it returns can become a number.

- [ ] **Step 3: Update the capability map**

In `docs/assets/build_capability_map.py`, remove web search from `NOT_WIRED` so
the banner stops saying it is unreachable, then regenerate:

```bash
uv run --project backend python docs/assets/build_capability_map.py
```

```bash
cd frontend && node scripts/render-social.mjs
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md docs/assets
git commit -F <message file>
```

Subject: `docs: record that web search reaches a run`.

---

## Verification

```bash
cd backend && uv run pytest -q && uv run ruff check src tests alembic && uv run mypy src
```

```bash
cd frontend && npx vitest run && npx tsc --noEmit
```

Then by hand, which is the part that proves it: start the stack with
`.\start.ps1`, create a project with **web search enabled** and a planner model
assigned, and ask the question that failed before —
**"How has the National Stock Exchange of India grown over the last 30 years?"**
The trace should carry a `web_search:duckduckgo` step whose response mentions
`^NSEI`, and the plan should name a ticker that resolves. If the plan still says
`NSEI`, the feature works and the model ignored the context — report that
honestly rather than reading it as a failure of the wiring.
