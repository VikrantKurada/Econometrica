# MCP Research Phase — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a project configure MCP servers, and have a run's dedicated research agent call their allowlisted tools before planning — gathering *context* (never numbers) into the Planner's prompt.

**Architecture:** MCP is a fourth context channel beside retrieval and web search: read, not computed. A new `researcher` model role runs a bounded tool-calling loop over the project's allowlisted MCP tools, reusing the LLM layer's existing tool transport, and appends a text summary to the Planner's context. The connection layer and the loop keep `agents/` off both `db.models` and the MCP SDK's transport types: `mcp/config.py` and `mcp/connect.py` hold the typed config and the transports, `agents/researcher.py` runs the loop over a `Connector`, and the router translates ORM configs and wires it in.

**Tech Stack:** Python 3.12, FastAPI, `mcp==1.28.1` (stdio + streamable-http client transports, in-memory `FastMCP` for tests), the existing `llm` tool-calling types, SQLAlchemy + Alembic, pytest (async), `uv`.

**Design note:** `docs/plans/2026-07-31-econometrica-mcp-design.md`.

## Global Constraints

- **TDD, strictly.** Write the failing test, run it, watch it fail with the *expected* error, then implement.
- **`agents/` imports neither `db.models` nor the MCP SDK's transport types.** The `Researcher` speaks `McpClient` / `Connector` (from `mcp/`) and the `llm` types; the SDK's `ClientSession`/`stdio_client`/`streamablehttp_client` stop in `mcp/connect.py`.
- **MCP output is context, never a number.** The grounding gate is untouched; the research summary reaches the **Planner only, never the Narrator**. A test asserts a number quoted from a tool's output stays ungrounded.
- **Default-deny holds.** Only allowlisted tools are ever offered to the model, and `McpClient.call` gates again before the server is asked.
- Backend commands run under `uv` from `backend/`. **DB tests need Postgres:** `docker compose up -d db --wait`.
- **`mypy --strict` clean on `src`; ruff clean.** Commit with `git commit -F <file>` (heredoc), ending each body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Branch:** `feat/foundation`. Loop cap: `MAX_RESEARCH_ROUNDS = 4`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/econometrica/mcp/config.py` | New. `McpServerConfig` typed, `from_mapping`. | 1 |
| `tests/mcp/test_config.py` | New. Config validation. | 1 |
| `src/econometrica/mcp/connect.py` | New. `connect_server`, `Connector` protocol, `McpConnector`. | 2 |
| `tests/mcp/test_connect.py` + `tests/mcp/fixtures/echo_server.py` | New. Real stdio round-trip + degradation. | 2 |
| `src/econometrica/agents/researcher.py` | New. `ResearchOutcome`, the `Researcher` loop. | 3 |
| `tests/agents/test_researcher.py` | New. The loop over in-memory servers. | 3 |
| `src/econometrica/db/models/run.py`, `agents/trace.py`, `mcp/client.py`, `alembic/versions/<rev>_researcher_step_agent.py`, `tests/db/test_run_model.py` | Modify/new. `researcher` trace agent + migration. | 4 |
| `src/econometrica/agents/orchestrator.py`, `tests/agents/test_orchestrator.py` | Modify. `researcher` param + `_research_context`. | 5 |
| `src/econometrica/api/routers/runs.py`, `tests/api/test_runs.py` | Modify. Translate configs, gate, wire. | 6 |
| `src/econometrica/api/routers/mcp.py`, `src/econometrica/schemas/mcp.py`, `src/econometrica/schemas/project.py`, `src/econometrica/main.py`, `tests/api/test_mcp.py` | New/modify. Discovery route + typed `mcp_servers`. | 7 |
| `CLAUDE.md`, `README.md` | Modify. Record MCP wired. | 8 |

---

## Task 1: `McpServerConfig` — a typed server config

**Files:**
- Create: `src/econometrica/mcp/config.py`
- Test: `tests/mcp/test_config.py`

**Interfaces:**
- Produces: `McpServerConfig` (Pydantic) with `name: str`, `transport: Literal["stdio","http"]`, `command`/`args`/`env` (stdio), `url`/`headers` (http); a validator requiring `command` for stdio and `url` for http; `from_mapping(data: dict) -> McpServerConfig`.

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_config.py`:

```python
"""The typed MCP server config, parsed from the project's JSONB list."""

import pytest

from econometrica.mcp.config import McpServerConfig


def test_a_stdio_server_needs_a_command():
    with pytest.raises(ValueError, match="command"):
        McpServerConfig(name="files", transport="stdio")


def test_an_http_server_needs_a_url():
    with pytest.raises(ValueError, match="url"):
        McpServerConfig(name="wiki", transport="http")


def test_an_unknown_transport_is_rejected():
    with pytest.raises(ValueError):
        McpServerConfig(name="x", transport="carrier-pigeon", command="run")


def test_a_valid_stdio_config_round_trips_from_a_mapping():
    config = McpServerConfig.from_mapping(
        {"name": "files", "transport": "stdio", "command": "uvx", "args": ["files-mcp"]}
    )
    assert config.name == "files"
    assert config.command == "uvx"
    assert config.args == ["files-mcp"]


def test_a_valid_http_config_round_trips_from_a_mapping():
    config = McpServerConfig.from_mapping(
        {"name": "wiki", "transport": "http", "url": "https://mcp.example/api"}
    )
    assert config.transport == "http"
    assert config.url == "https://mcp.example/api"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'econometrica.mcp.config'`.

- [ ] **Step 3: Implement**

Create `src/econometrica/mcp/config.py`:

```python
"""How a project names an MCP server, typed.

`project.mcp_servers` is a JSONB list; this is the shape each entry must take
before anything tries to connect from it. Kept free of the MCP SDK — the SDK's
transport types belong to `connect.py`, this is just the user's declared intent.
"""

from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator


class McpServerConfig(BaseModel):
    """One MCP server a project may connect to."""

    name: str = Field(min_length=1)
    transport: Literal["stdio", "http"]

    #: stdio: a local command, spawned with host privileges — see the design
    #: note's trust model. Not sandboxed; configuring one is trusting it.
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)

    #: http: an already-running server reached by URL.
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def transport_fields_must_be_present(self) -> Self:
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"stdio server {self.name!r} needs a command to spawn")
        if self.transport == "http" and not self.url:
            raise ValueError(f"http server {self.name!r} needs a url to connect to")
        return self

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "McpServerConfig":
        """Parse one entry from `project.mcp_servers`."""
        return cls.model_validate(data)
```

- [ ] **Step 4: Run to verify pass; lint; type-check**

Run: `uv run pytest tests/mcp/test_config.py -q`, `uv run ruff check src/econometrica/mcp/config.py tests/mcp/test_config.py`, `uv run mypy src`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
cat > /tmp/m1.txt <<'EOF'
feat(mcp): a typed server config

project.mcp_servers is an untyped JSONB list; McpServerConfig is the shape
each entry must take before anything connects. A stdio server needs a
command, an http server a url, and an unknown transport is refused. Kept
free of the MCP SDK -- the transport types belong to connect.py.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/mcp/config.py backend/tests/mcp/test_config.py
git commit -F /tmp/m1.txt && rm -f /tmp/m1.txt
```

---

## Task 2: The connection layer — `mcp/connect.py`

**Files:**
- Create: `src/econometrica/mcp/connect.py`
- Create: `tests/mcp/fixtures/echo_server.py` (a real stdio FastMCP server)
- Test: `tests/mcp/test_connect.py`

**Interfaces:**
- Consumes: `McpServerConfig` (Task 1); `Allowlist`, `McpClient` (existing); the SDK's `stdio_client`, `StdioServerParameters`, `streamablehttp_client`, `ClientSession`.
- Produces:
  - `connect_server(config, allowlist) -> AbstractAsyncContextManager[McpClient]`.
  - `class Connector(Protocol)` with `def open(self) -> AbstractAsyncContextManager[list[McpClient]]`.
  - `class McpConnector(Connector)` built from `list[McpServerConfig]` + `Allowlist`; `open()` connects every server it can, yields their gated `McpClient`s, and closes them on exit — an unreachable server is skipped, not raised.

- [ ] **Step 1: Write the fixture server**

Create `tests/mcp/fixtures/echo_server.py`:

```python
"""A real MCP server over stdio, for the connection round-trip test."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo")


@server.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo: {text}"


if __name__ == "__main__":
    server.run()  # stdio by default
```

- [ ] **Step 2: Write the failing tests**

Create `tests/mcp/test_connect.py`:

```python
"""Connecting to real MCP servers, and degrading when one will not."""

import sys
from pathlib import Path

import pytest

from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.config import McpServerConfig
from econometrica.mcp.connect import McpConnector, connect_server

_ECHO = Path(__file__).parent / "fixtures" / "echo_server.py"


def _stdio(name="echo") -> McpServerConfig:
    return McpServerConfig(
        name=name, transport="stdio", command=sys.executable, args=[str(_ECHO)]
    )


async def test_connect_stdio_round_trips_against_a_real_server():
    """Spawns the fixture over stdio, discovers and calls a tool. Offline, but a
    real subprocess and a real session — the transport code cannot be proven by
    the in-memory harness, which hands back a session already connected."""
    async with connect_server(_stdio(), Allowlist(["echo:echo"])) as client:
        found = {t.name for t in await client.discover()}
        assert "echo" in found

        call = await client.call("echo", {"text": "hi"})
    assert "echo: hi" in call.output


async def test_the_connector_yields_a_gated_client_per_reachable_server():
    connector = McpConnector([_stdio("echo")], Allowlist(["echo:echo"]))

    async with connector.open() as clients:
        assert [c.server for c in clients] == ["echo"]
        call = await clients[0].call("echo", {"text": "x"})
    assert "echo: x" in call.output


async def test_an_unreachable_server_is_skipped_not_raised():
    """A bad command must not take the whole connect down — the other servers,
    and the run, survive it."""
    good = _stdio("echo")
    bad = McpServerConfig(name="broken", transport="stdio", command="does-not-exist-xyz")
    connector = McpConnector([bad, good], Allowlist(["echo:echo"]))

    async with connector.open() as clients:
        # The broken server is dropped; the good one remains.
        assert [c.server for c in clients] == ["echo"]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/mcp/test_connect.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'econometrica.mcp.connect'`.

- [ ] **Step 4: Implement `mcp/connect.py`**

```python
"""Building live MCP sessions from a project's server configs.

The MCP SDK's transport types stop here, the way a vendor SDK's stop in
`llm/providers/`. Above this, everything speaks `McpClient`. A server is
connected only for the research phase and closed after — it is not a resident
dependency of the app.

A stdio server is an arbitrary local command spawned with host privileges (see
the design note's trust model); this module spawns it, it does not sandbox it.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.client import McpClient
from econometrica.mcp.config import McpServerConfig

logger = logging.getLogger(__name__)


@asynccontextmanager
async def connect_server(
    config: McpServerConfig, allowlist: Allowlist
) -> AsyncIterator[McpClient]:
    """A connected, allowlist-gated client for one server."""
    async with AsyncExitStack() as stack:
        if config.transport == "stdio":
            params = StdioServerParameters(
                command=config.command, args=config.args, env=config.env or None
            )
            streams = await stack.enter_async_context(stdio_client(params))
        else:
            streams = await stack.enter_async_context(
                streamablehttp_client(config.url, headers=config.headers or None)
            )
        # stdio yields (read, write); http yields (read, write, get_session_id).
        read, write = streams[0], streams[1]
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        yield McpClient(session=session, server=config.name, allowlist=allowlist)


class Connector(Protocol):
    """What the Researcher sees: a way to open its tools and close them after."""

    def open(self) -> "AsyncExitStack | object":  # AbstractAsyncContextManager[list[McpClient]]
        ...


class McpConnector:
    """Opens every server a project configured, degrading past the ones it can't."""

    def __init__(self, configs: list[McpServerConfig], allowlist: Allowlist) -> None:
        self._configs = configs
        self._allowlist = allowlist

    @asynccontextmanager
    async def open(self) -> AsyncIterator[list[McpClient]]:
        async with AsyncExitStack() as stack:
            clients: list[McpClient] = []
            for config in self._configs:
                try:
                    client = await stack.enter_async_context(
                        connect_server(config, self._allowlist)
                    )
                except Exception as exc:
                    # An unreachable server is a finding, not a failure: the run
                    # proceeds with the tools it could reach.
                    logger.warning("mcp server %s could not be connected: %s", config.name, exc)
                    continue
                clients.append(client)
            yield clients
```

Note the `Connector` protocol's `open` return type is annotated loosely to avoid importing `AbstractAsyncContextManager` generics into the protocol; `McpConnector.open` is a concrete `@asynccontextmanager`. If mypy objects, type the protocol method as `-> AbstractContextManager[Any]` from `contextlib` — the Researcher only ever uses it as `async with connector.open() as clients`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/mcp/test_connect.py -q`
Expected: PASS. If the stdio spawn is slow or flaky on this machine, keep it — a real transport test is the point — but note the subprocess in the commit.

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src/econometrica/mcp/connect.py tests/mcp/test_connect.py` and `uv run mypy src`
Expected: clean. Resolve any protocol-typing objection as noted above.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/m2.txt <<'EOF'
feat(mcp): connect live sessions from server configs

connect_server builds a gated McpClient over the SDK's stdio or
streamable-http transport; McpConnector opens every configured server it
can and skips the ones it cannot, so an unreachable server degrades the
research phase rather than failing the run. The SDK's transport types stop
here. A stdio server is spawned, not sandboxed -- trust is decided at
config time. Tested against a real FastMCP fixture over a real subprocess,
because the in-memory harness cannot exercise the transport code.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/mcp/connect.py backend/tests/mcp/test_connect.py \
  backend/tests/mcp/fixtures/echo_server.py
git commit -F /tmp/m2.txt && rm -f /tmp/m2.txt
```

---

## Task 3: The Research agent and its loop

**Files:**
- Create: `src/econometrica/agents/researcher.py`
- Test: `tests/agents/test_researcher.py`

**Interfaces:**
- Consumes: `LLMProvider`, `Message`, `ToolSpec`, `ToolCall`, `Completion` (llm types); `Connector`, `McpClient` (mcp); `StepRecord` (trace); `ToolNotAllowedError`, `McpUnavailableError`.
- Produces:
  - `ResearchOutcome(summary: str, calls: list[McpCall], records: list[StepRecord], failed: bool, detail: str)` with `as_context() -> str`.
  - `class Researcher(provider, model, connector, *, max_rounds=MAX_RESEARCH_ROUNDS)` with `async def research(self, question: str) -> ResearchOutcome`.

- [ ] **Step 1: Write the failing tests**

Create `tests/agents/test_researcher.py`:

```python
"""The research loop: a model calls allowlisted MCP tools, then summarises."""

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from econometrica.agents.researcher import Researcher
from econometrica.llm.types import Completion, ToolCall, Usage
from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.client import McpClient

EXECUTED: list[str] = []


def build_server() -> FastMCP:
    server = FastMCP("facts")

    @server.tool()
    def lookup(symbol: str) -> str:
        """Look up a symbol."""
        EXECUTED.append(f"lookup:{symbol}")
        return f"{symbol} is the Nifty 50 index"

    @server.tool()
    def wipe() -> str:
        """Offered, never allowed."""
        EXECUTED.append("wipe")
        return "wiped"

    return server


class ScriptedModel:
    """Returns pre-built completions in order and records the tools it was given."""

    name = "scripted"

    def __init__(self, completions: list[Completion]) -> None:
        self._completions = completions
        self.tools_seen: list[list[str]] = []
        self._i = 0

    async def complete(self, messages, *, model, tools=None, **kw) -> Completion:
        self.tools_seen.append([t.name for t in (tools or [])])
        completion = self._completions[self._i]
        self._i += 1
        return completion


def tool_call(name, args, id="c1") -> Completion:
    return Completion(tool_calls=[ToolCall(id=id, name=name, arguments=args)], usage=Usage())


def text(content) -> Completion:
    return Completion(content=content, usage=Usage())


class OneServerConnector:
    """Yields a McpClient over an in-memory FastMCP, with the given allowlist."""

    def __init__(self, allowlist: list[str]) -> None:
        self._allowlist = Allowlist(allowlist)

    def open(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _open():
            async with create_connected_server_and_client_session(build_server()) as session:
                yield [McpClient(session=session, server="facts", allowlist=self._allowlist)]

        return _open()


def setup_function():
    EXECUTED.clear()


async def test_the_model_calls_an_allowed_tool_and_summarises():
    model = ScriptedModel(
        [tool_call("facts__lookup", {"symbol": "^NSEI"}), text("The symbol is the Nifty 50.")]
    )
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    outcome = await researcher.research("What is ^NSEI?")

    assert EXECUTED == ["lookup:^NSEI"]
    assert "Nifty 50" in outcome.summary
    assert outcome.failed is False
    assert "read, not computed" in outcome.as_context().lower()


async def test_only_allowlisted_tools_are_offered_to_the_model():
    model = ScriptedModel([text("nothing to do")])
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    await researcher.research("q")

    # `wipe` exists on the server but is not allowlisted, so it is never offered.
    assert model.tools_seen[0] == ["facts__lookup"]


async def test_a_hallucinated_tool_is_fed_back_as_an_error_not_run():
    model = ScriptedModel(
        [tool_call("facts__wipe", {}), text("could not do that")]
    )
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    outcome = await researcher.research("q")

    assert EXECUTED == []  # the disallowed tool never ran
    assert "could not do that" in outcome.summary


async def test_the_loop_stops_at_the_round_cap_and_still_summarises():
    # Always asks for a tool; the cap must end it with a tool-free summary call.
    calls = [tool_call("facts__lookup", {"symbol": "x"}, id=f"c{i}") for i in range(10)]
    model = ScriptedModel([*calls, text("here is what I found")])
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]), max_rounds=3)

    outcome = await researcher.research("q")

    assert "what I found" in outcome.summary
    assert len(outcome.calls) <= 3  # bounded


async def test_a_run_that_calls_nothing_returns_its_text_in_one_round():
    model = ScriptedModel([text("no external data needed")])
    researcher = Researcher(model, "m", OneServerConnector(["facts:lookup"]))

    outcome = await researcher.research("q")

    assert outcome.summary == "no external data needed"
    assert outcome.calls == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_researcher.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'econometrica.agents.researcher'`.

- [ ] **Step 3: Implement `agents/researcher.py`**

```python
"""The Research agent: a bounded tool-calling loop over MCP tools.

Not an `Agent[T]` — that base returns one validated JSON object. This is a
free-form conversation that ends in prose: the model is offered the project's
allowlisted MCP tools, calls what it chooses, sees the results, and eventually
answers without a tool call. Its answer is *context* for the Planner, read not
computed — the grounding gate blocks any number quoted from it, exactly as it
does for web search and retrieval.

`agents/` imports no `db.models` and no MCP SDK transport type: this speaks
`McpClient` and a `Connector`, both from `mcp/`.
"""

import re
from dataclasses import dataclass, field

from econometrica.agents.trace import StepRecord
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Message, ToolSpec
from econometrica.mcp.client import McpCall, McpClient, McpUnavailableError
from econometrica.mcp.connect import Connector

MAX_RESEARCH_ROUNDS = 4

#: Model tool names must be `[A-Za-z0-9_-]+`, but the allowlist is `server:tool`.
#: Offered names are `server__tool`; a map reconstructs the call, so the exact
#: spelling never has to round-trip.
_SEPARATOR = "__"

_SYSTEM = """\
You are the Researcher in an econometrics workbench. Before an analysis is
planned, you may call the tools below to gather context the Planner needs —
a data definition, a ticker, a methodology note, an internal figure to look up.

Call a tool when it helps. When you have what you need, reply with a short plain
summary of what you found and stop calling tools. Nothing you return is a
computed result; it is context read from a tool, and a later agent decides what
to compute.
"""

_SUMMARISE = "Summarise what you found, in a few sentences. Do not call any tool."


@dataclass
class ResearchOutcome:
    summary: str = ""
    calls: list[McpCall] = field(default_factory=list)
    records: list[StepRecord] = field(default_factory=list)
    failed: bool = False
    detail: str = ""

    def as_context(self) -> str:
        if not self.summary.strip():
            return ""
        return (
            "# From MCP tools — read, not computed.\n"
            "Nothing here is a result. Do not cite a number from it.\n\n"
            + self.summary
        )


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


class Researcher:
    role = "researcher"

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        connector: Connector,
        *,
        max_rounds: int = MAX_RESEARCH_ROUNDS,
    ) -> None:
        self.provider = provider
        self.model = model
        self._connector = connector
        self._max_rounds = max_rounds

    async def research(self, question: str) -> ResearchOutcome:
        outcome = ResearchOutcome()
        async with self._connector.open() as clients:
            offered, index = await self._offer(clients, outcome)
            if not offered:
                # Nothing allowed or reachable: no research to do.
                return outcome

            messages = [Message.system(_SYSTEM), Message.user(f"Question: {question}")]
            for _ in range(self._max_rounds):
                completion = await self.provider.complete(
                    messages, model=self.model, tools=offered
                )
                outcome.records.append(_llm_step(completion, self.model, self.provider))
                if not completion.tool_calls:
                    outcome.summary = completion.content
                    return outcome

                messages.append(Message.assistant(completion.content, completion.tool_calls))
                for tc in completion.tool_calls:
                    result = await self._run(tc, index, outcome)
                    messages.append(Message.tool_result(tc.id, result))

            # Hit the cap while still calling tools: one tool-free summary call.
            messages.append(Message.user(_SUMMARISE))
            final = await self.provider.complete(messages, model=self.model)
            outcome.records.append(_llm_step(final, self.model, self.provider))
            outcome.summary = final.content
        return outcome

    async def _offer(
        self, clients: list[McpClient], outcome: ResearchOutcome
    ) -> tuple[list[ToolSpec], dict[str, tuple[McpClient, str]]]:
        """The allowlisted tools, as ToolSpecs, plus the map back to (client, tool)."""
        specs: list[ToolSpec] = []
        index: dict[str, tuple[McpClient, str]] = {}
        for client in clients:
            try:
                discovered = await client.discover()
            except McpUnavailableError as exc:
                outcome.detail = str(exc)
                continue
            for tool in discovered:
                if not tool.allowed:
                    continue
                offered_name = f"{_safe(client.server)}{_SEPARATOR}{_safe(tool.name)}"
                specs.append(
                    ToolSpec(
                        name=offered_name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                    )
                )
                index[offered_name] = (client, tool.name)
        return specs, index

    async def _run(self, tool_call, index, outcome: ResearchOutcome) -> str:
        """Execute one requested call, or turn its failure into an error result."""
        target = index.get(tool_call.name)
        if target is None:
            return f"error: {tool_call.name!r} is not an available tool"
        client, tool = target
        try:
            call = await client.call(tool, tool_call.arguments)
        except (McpUnavailableError, Exception) as exc:  # allowlist + transport
            return f"error: {exc}"
        outcome.calls.append(call)
        outcome.records.append(call.to_step_record())
        return call.output


def _llm_step(completion, model: str, provider) -> StepRecord:
    return StepRecord(
        agent="researcher",
        kind="llm",
        status="ok",
        provider=getattr(provider, "name", None),
        model=model,
        usage=completion.usage,
        latency_ms=completion.latency_ms,
        response=completion.content,
    )
```

> Note the `except (McpUnavailableError, Exception)` catches the allowlist's
> `ToolNotAllowedError` (a `PermissionError`) and transport errors alike, feeding
> both back to the model. `Exception` alone would do; both are named for the
> reader. If ruff's `BLE001` objects, catch `(McpUnavailableError, PermissionError)`.

- [ ] **Step 4: Run the tests; lint; type-check**

Run: `uv run pytest tests/agents/test_researcher.py -q`, then `uv run ruff check src/econometrica/agents/researcher.py tests/agents/test_researcher.py`, `uv run mypy src`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
cat > /tmp/m3.txt <<'EOF'
feat(agents): the Research agent runs a bounded MCP tool-calling loop

A new researcher role offers the model only allowlisted tools, runs what it
chooses through the gated McpClient, feeds results back, and stops when the
model answers without a tool or at max_rounds -- on the cap, one tool-free
call gets a clean summary. The summary is context for the Planner, read not
computed. A disallowed or hallucinated tool becomes an error result, never
a run. agents/ imports McpClient and a Connector, no db and no SDK
transport. Tested with a scripted model over a real in-memory server.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/agents/researcher.py backend/tests/agents/test_researcher.py
git commit -F /tmp/m3.txt && rm -f /tmp/m3.txt
```

---

## Task 4: `researcher` as a trace-step agent

Mirror of the `query_writer` migration. `researcher` LLM turns and MCP tool steps both carry `agent="researcher"`, so the value must be legal in `run_steps`, and `McpCall.to_step_record`'s placeholder `agent="econometrician"` becomes `researcher`.

**Files:**
- Modify: `src/econometrica/db/models/run.py` (`STEP_AGENTS`), `src/econometrica/agents/trace.py` (`AGENTS`), `src/econometrica/mcp/client.py` (`to_step_record` agent)
- Create: `alembic/versions/<rev>_researcher_step_agent.py`
- Test: `tests/db/test_run_model.py`

- [ ] **Step 1: Write the failing DB test**

Add to `tests/db/test_run_model.py`:

```python
async def test_a_researcher_step_is_accepted(session):
    """The research agent's turns and MCP calls have to reach the trace."""
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="researcher", kind="llm", status="ok"))
    await session.flush()
```

- [ ] **Step 2: Run it (expect the CHECK violation)**

Run: `docker compose up -d db --wait` then `uv run pytest tests/db/test_run_model.py::test_a_researcher_step_is_accepted -q`
Expected: FAIL — `IntegrityError`, violates `ck_run_steps_agent_known`.

- [ ] **Step 3: Widen the vocabularies and repoint the MCP step**

`src/econometrica/db/models/run.py` — add `"researcher"` to `STEP_AGENTS`.
`src/econometrica/agents/trace.py` — add `"researcher"` to `AGENTS` with a one-line comment.
`src/econometrica/mcp/client.py` — in `McpCall.to_step_record`, change `agent="econometrician"` to `agent="researcher"` (the research agent drives every MCP call; the placeholder predated that decision).

- [ ] **Step 4: DB test passes; migration gate fails**

Run: `uv run pytest tests/db/test_run_model.py::test_a_researcher_step_is_accepted tests/db/test_migrations.py -q`
Expected: the model test PASSES; `test_every_value_in_a_check_constraint_vocabulary_reaches_a_migration` FAILS (value in no migration).

- [ ] **Step 5: Write the migration**

Create `alembic/versions/<rev>_researcher_step_agent.py` — pick a fresh 12-hex `<rev>`, `down_revision = "f0a1c2d3e4b5"` (confirm with `uv run alembic heads`). Structure copied verbatim from `f0a1c2d3e4b5_query_writer_step_agent.py`, with:

```python
_AGENTS = (
    "planner", "data_steward", "econometrician", "validator", "narrator",
    "quant_coder", "query_writer", "researcher",
)
_PREVIOUS = _AGENTS[:-1]
```

`upgrade()` drops and recreates `ck_run_steps_agent_known` over `_AGENTS`; `downgrade()` deletes `agent = 'researcher'` rows first, then recreates over `_PREVIOUS`. Same docstrings as the query_writer revision (autogenerate cannot see a CHECK changed on an existing table).

- [ ] **Step 6: Verify the gates, the chain, and drift**

Run:
```bash
uv run pytest tests/db/test_migrations.py -q
uv run alembic heads      # single head: the new rev
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head   # roundtrip
uv run alembic check      # no drift
uv run pytest tests/db tests/mcp -q
uv run ruff check src alembic && uv run mypy src
```
Expected: all green; single head; no drift. (`tests/mcp` re-run confirms the `to_step_record` agent change did not break the client tests, which assert `tool`/`kind`/`status`, not `agent`.)

- [ ] **Step 7: Commit**

```bash
cat > /tmp/m4.txt <<'EOF'
feat(db): allow researcher as a run step agent

The research agent's model turns and its MCP tool calls are traced as
agent="researcher", so the value has to be legal in run_steps. Widening
STEP_AGENTS alone is the quant_coder trap -- create_all accepts it while a
migrated database rejects it -- so a hand-written revision widens
ck_run_steps_agent_known, gated by a real insert and the constraint-value
test. McpCall.to_step_record's placeholder agent="econometrician" becomes
"researcher", the agent that actually drives every MCP call.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/db/models/run.py backend/src/econometrica/agents/trace.py \
  backend/src/econometrica/mcp/client.py backend/alembic/versions/*researcher_step_agent.py \
  backend/tests/db/test_run_model.py
git commit -F /tmp/m4.txt && rm -f /tmp/m4.txt
```

---

## Task 5: Orchestrator wiring

**Files:**
- Modify: `src/econometrica/agents/orchestrator.py` (import `Researcher`, `researcher` param, `_research_context`, call in `_pipeline`)
- Test: `tests/agents/test_orchestrator.py` (`SpyResearcher` + tests)

**Interfaces:**
- Consumes: `Researcher`, `ResearchOutcome` (Task 3).
- Produces: `Orchestrator.__init__(..., researcher: Researcher | None = None)`; `_research_context` run before `_retrieval_context`.

- [ ] **Step 1: Write the failing tests**

In `tests/agents/test_orchestrator.py`, add near the retrieval helpers:

```python
class SpyResearcher:
    def __init__(self, *, summary="", fail=False) -> None:
        from econometrica.agents.trace import StepRecord

        self._summary = summary
        self._fail = fail
        self.asked: list[str] = []
        self._Step = StepRecord

    async def research(self, question: str):
        from econometrica.agents.researcher import ResearchOutcome

        self.asked.append(question)
        rec = self._Step(agent="researcher", kind="llm", status="ok", response=self._summary)
        return ResearchOutcome(
            summary=self._summary, records=[rec], failed=self._fail
        )


def research_step(outcome):
    return next(s for s in outcome.trace if s.agent == "researcher")


async def test_a_research_summary_reaches_the_planner():
    spy = SpyResearcher(summary="The internal universe lists ^NSEI as the Nifty 50.")
    orchestrator, fakes = build(researcher=spy)

    await orchestrator.run(QUESTION)

    assert spy.asked == [QUESTION]
    assert "read, not computed" in planner_prompt(fakes).lower()
    assert "^NSEI" in planner_prompt(fakes)


async def test_without_a_researcher_nothing_is_researched():
    orchestrator, _ = build()

    outcome = await orchestrator.run(QUESTION)

    assert not any(s.agent == "researcher" for s in outcome.trace)


async def test_the_research_steps_precede_the_plan():
    spy = SpyResearcher(summary="context")
    orchestrator, _ = build(researcher=spy)

    outcome = await orchestrator.run(QUESTION)

    research = outcome.trace.index(research_step(outcome))
    plan = next(i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm")
    assert research < plan
```

Add `researcher: object | None = None` to `build()` and pass `researcher=researcher` to the `Orchestrator(...)` call.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_orchestrator.py -k "research" -q`
Expected: FAIL — `unexpected keyword argument 'researcher'`.

- [ ] **Step 3: Implement**

In `src/econometrica/agents/orchestrator.py`:

Import: `from econometrica.agents.researcher import Researcher` (place in agents import block, alphabetical order — before `schemas`).

Constructor param (after `retriever: Retriever | None = None,`): `researcher: Researcher | None = None,`. Store it with a comment mirroring `retriever`.

In `_pipeline`, run research first of the three context phases:

```python
        context = await self._research_context(question, context, trace)
        context = await self._retrieval_context(question, context, trace)
        context = await self._search_context(question, context, trace)
```

Add the method beside `_retrieval_context`:

```python
    async def _research_context(
        self, question: str, context: str, trace: TraceBuilder
    ) -> str:
        """MCP tools, called by a research agent, as context for the Planner.

        Planner only, never the Narrator — the grounding-gate reason web search
        and retrieval are withheld from it. A research phase that fails or finds
        nothing degrades the run: MCP is context, and losing it is the smaller
        loss.
        """
        if self.researcher is None:
            return context

        outcome = await self.researcher.research(question)
        for record in outcome.records:
            record.parent = trace.last
            trace.add(record)

        found = outcome.as_context()
        if not found:
            return context
        return f"{context}\n\n{found}" if context else found
```

- [ ] **Step 4: Run the tests; lint; type-check**

Run: `uv run pytest tests/agents/test_orchestrator.py -q`, `uv run ruff check src tests/agents/test_orchestrator.py`, `uv run mypy src`
Expected: PASS; clean.

- [ ] **Step 5: Commit**

```bash
cat > /tmp/m5.txt <<'EOF'
feat(runs): a run researches MCP tools before planning

The orchestrator gains a researcher, passed in like retriever, and a
_research_context that runs first of the three context phases: it calls the
research agent, records its researcher turns and MCP tool steps, and
appends the summary to the Planner's context. Never the Narrator -- same
grounding-gate reason. A failed or empty research degrades the run.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/agents/orchestrator.py backend/tests/agents/test_orchestrator.py
git commit -F /tmp/m5.txt && rm -f /tmp/m5.txt
```

---

## Task 6: Router wiring and gating

**Files:**
- Modify: `src/econometrica/api/routers/runs.py`
- Test: `tests/api/test_runs.py`

**Interfaces:**
- Consumes: `McpServerConfig`, `McpConnector`, `Researcher`, `Allowlist`, `resolve_capabilities`.
- Produces: a run whose orchestrator has a `Researcher` exactly when MCP is on, a server is configured, the allowlist is non-empty, a `researcher` role is assigned, and its model supports tool-calling.

- [ ] **Step 1: Write the failing test**

In the MCP-adjacent part of `tests/api/test_runs.py`, add a test that a project configured for MCP builds a researcher. Because a full research run needs a reachable server, assert on **construction** via a monkeypatched `Researcher`, mirroring the search-provider construction test:

```python
async def test_a_run_builds_a_researcher_when_mcp_is_configured(client, scripted, monkeypatch):
    built: list[str] = []

    class DummyResearcher:
        def __init__(self, *a, **k):
            built.append("researcher")

        async def research(self, question):
            from econometrica.agents.researcher import ResearchOutcome

            return ResearchOutcome(summary="")

    monkeypatch.setattr("econometrica.api.routers.runs.Researcher", DummyResearcher)
    scripted.provider.responses = [json.dumps(PLAN), NARRATIVE]

    project = (await client.post("/api/projects", json={"name": "M"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "validation_tier": "single",
            "mcp_enabled": True,
            "mcp_servers": [{"name": "facts", "transport": "http", "url": "https://x/api"}],
            "mcp_allowlist": ["facts:lookup"],
            "model_assignments": {
                "planner": {"provider": "ollama", "model": "fake-1"},
                "narrator": {"provider": "ollama", "model": "fake-1"},
                "researcher": {"provider": "ollama", "model": "fake-1"},
            },
        },
    )
    chat = (await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})).json()

    response = await client.post(f"/api/chats/{chat['id']}/runs", json={"question": QUESTION})

    assert response.status_code == 200
    assert built == ["researcher"]


async def test_a_run_without_mcp_builds_no_researcher(client, scripted, monkeypatch):
    built: list[str] = []
    monkeypatch.setattr(
        "econometrica.api.routers.runs.Researcher",
        lambda *a, **k: built.append("x"),
    )
    chat_id = await make_chat(client)  # no mcp

    await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    assert built == []
```

> The `fake-1` model reports `tool_calling=True` (see `llm/fake.py`'s `list_models`), so the capability gate passes. The DummyResearcher's `research` returns an empty summary, so the run proceeds normally without reaching a server.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_runs.py -k "researcher" -q`
Expected: FAIL — no researcher is built (`built == []` where `["researcher"]` was expected).

- [ ] **Step 3: Wire `_build`**

In `src/econometrica/api/routers/runs.py`, add imports:
```python
from econometrica.agents.researcher import Researcher
from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.config import McpServerConfig
from econometrica.mcp.connect import McpConnector
```

After the `retriever` block, add:
```python
    # MCP research runs when the capability is on, a server is configured, the
    # allowlist permits something, a researcher is assigned, and its model can
    # call tools. Anything missing -> no researcher, and the run is unchanged.
    researcher = None
    allowlist = Allowlist.for_project(project)
    if (
        capabilities.mcp
        and (project.mcp_servers or [])
        and len(allowlist)
        and "researcher" in (project.model_assignments or {})
    ):
        try:
            r_provider, r_model = _bind("researcher", project, registry)
        except HTTPException:
            r_provider = None  # a misconfigured researcher degrades, like search
        if r_provider is not None and _supports_tools(r_provider, r_model):
            configs = _server_configs(project)
            researcher = Researcher(
                r_provider, r_model, McpConnector(configs, allowlist)
            )
```

Add two helpers near `_tier`:
```python
def _server_configs(project: Project) -> list[McpServerConfig]:
    """The project's typed server configs, dropping any that will not parse —
    a malformed entry costs itself, not the whole research phase."""
    configs: list[McpServerConfig] = []
    for entry in project.mcp_servers or []:
        try:
            configs.append(McpServerConfig.from_mapping(entry))
        except (ValueError, TypeError):
            continue
    return configs


def _supports_tools(provider: LLMProvider, model: str) -> bool:
    """Whether the assigned model can call tools at all; if not, research is
    skipped rather than looping uselessly."""
    try:
        infos = {m.id: m for m in run_sync(provider.list_models())}
    except Exception:
        return False
    info = infos.get(model)
    return bool(info and info.capabilities.tool_calling)
```

`_supports_tools` needs the model list, which is async. Since `_build` is already async, call it inline instead of a `run_sync` shim:
```python
        if r_provider is not None and await _supports_tools(r_provider, r_model):
```
and make `_supports_tools` `async def`, awaiting `provider.list_models()`. Drop the `run_sync` idea — it was a mistake; `_build` is async, so `await` directly.

Pass `researcher=researcher` to the `Orchestrator(...)` call (after `retriever=retriever,`).

- [ ] **Step 4: Run the tests; full runs suite; lint; types**

Run: `uv run pytest tests/api/test_runs.py -q`, `uv run ruff check src tests/api/test_runs.py`, `uv run mypy src`
Expected: PASS (existing + 2 new); clean.

- [ ] **Step 5: Commit**

```bash
cat > /tmp/m6.txt <<'EOF'
feat(runs): build a researcher when MCP is configured

_build constructs a Researcher only when the mcp capability is on, a
server is configured, the allowlist permits something, a researcher role
is assigned, and its model can call tools -- checked against the model's
reported capabilities. Anything missing, or a misconfigured researcher,
degrades to no research rather than failing the run, like the search
provider. The router translates the ORM configs into typed McpServerConfig
and builds the connector; agents/ stays off db.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/api/routers/runs.py backend/tests/api/test_runs.py
git commit -F /tmp/m6.txt && rm -f /tmp/m6.txt
```

---

## Task 7: Discovery route and typed `mcp_servers`

**Files:**
- Create: `src/econometrica/schemas/mcp.py` (`McpToolRead`, `McpServerToolsRead`)
- Create: `src/econometrica/api/routers/mcp.py` (`GET /api/projects/{id}/mcp/tools`)
- Modify: `src/econometrica/schemas/project.py` (type `mcp_servers` as `list[McpServerConfig]`)
- Modify: `src/econometrica/main.py` (register the router)
- Test: `tests/api/test_mcp.py`

**Interfaces:**
- Consumes: `McpServerConfig`, `McpConnector`, `Allowlist`, `get_project_or_404`.
- Produces: a discovery endpoint returning each configured server's tools with `allowed` flags; validated `mcp_servers` on project update.

- [ ] **Step 1: Write the failing test**

Create `tests/api/test_mcp.py` — discovery over the stdio echo fixture, asserting the tool is listed with its `allowed` flag from the project's allowlist:

```python
"""Discovering a project's MCP tools, to build the allowlist. Listing is not
permitting: a tool shows as not-allowed and stays uncallable."""

import sys
from pathlib import Path

_ECHO = str(Path(__file__).resolve().parents[1] / "mcp" / "fixtures" / "echo_server.py")


async def _project_with_echo(client, allowlist):
    project = (await client.post("/api/projects", json={"name": "MCP"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "mcp_enabled": True,
            "mcp_servers": [
                {"name": "echo", "transport": "stdio", "command": sys.executable, "args": [_ECHO]}
            ],
            "mcp_allowlist": allowlist,
        },
    )
    return project["id"]


async def test_discovery_lists_tools_with_their_allowed_flag(client):
    project_id = await _project_with_echo(client, ["echo:echo"])

    response = await client.get(f"/api/projects/{project_id}/mcp/tools")

    assert response.status_code == 200
    servers = {s["server"]: s for s in response.json()}
    tools = {t["name"]: t for t in servers["echo"]["tools"]}
    assert tools["echo"]["allowed"] is True


async def test_a_tool_not_in_the_allowlist_shows_as_not_allowed(client):
    project_id = await _project_with_echo(client, [])  # allow nothing

    response = await client.get(f"/api/projects/{project_id}/mcp/tools")

    tools = {t["name"]: t for s in response.json() for t in s["tools"]}
    assert tools["echo"]["allowed"] is False


async def test_an_invalid_server_config_is_rejected_on_update(client):
    project = (await client.post("/api/projects", json={"name": "Bad"})).json()

    response = await client.patch(
        f"/api/projects/{project['id']}",
        json={"mcp_servers": [{"name": "x", "transport": "stdio"}]},  # no command
    )

    assert response.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_mcp.py -q`
Expected: FAIL — the route 404s (not registered) and the invalid-config update is accepted (422 not yet enforced).

- [ ] **Step 3: Type `mcp_servers` on the project schema**

In `src/econometrica/schemas/project.py`, change `mcp_servers: list[Any] | None` (update) and `list[Any]` (read) to `list[McpServerConfig]`. Import `McpServerConfig`. This makes an invalid entry a 422 at the boundary. Keep `ProjectRead.mcp_servers` as `list[McpServerConfig]` too; existing rows are valid because nothing wrote an invalid one before typing existed — but if a read must not fail on legacy data, keep the read side `list[Any]` and validate only on update. **Decision: validate on update (`ProjectUpdate`), keep `ProjectRead.mcp_servers: list[Any]`** — reads must not fail on out-of-band rows, the same reason `validation_tier` is a plain `str` on read.

- [ ] **Step 4: Write the schema and router**

Create `src/econometrica/schemas/mcp.py`:

```python
"""What the MCP discovery endpoint returns."""

from typing import Any

from pydantic import BaseModel


class McpToolRead(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    allowed: bool


class McpServerToolsRead(BaseModel):
    server: str
    tools: list[McpToolRead]
    #: Set when the server could not be reached; `tools` is then empty.
    error: str = ""
```

Create `src/econometrica/api/routers/mcp.py`:

```python
"""Discovering a project's MCP tools. Listing is how a user builds the
allowlist — and showing a tool never makes it callable."""

from uuid import UUID

from fastapi import APIRouter

from econometrica.api.deps import SessionDep, get_project_or_404
from econometrica.db.models import Project
from econometrica.mcp.allowlist import Allowlist
from econometrica.mcp.config import McpServerConfig
from econometrica.mcp.connect import connect_server
from econometrica.schemas.mcp import McpServerToolsRead, McpToolRead

router = APIRouter(prefix="/api/projects", tags=["mcp"])


@router.get("/{project_id}/mcp/tools", response_model=list[McpServerToolsRead])
async def discover_tools(project_id: UUID, session: SessionDep) -> list[McpServerToolsRead]:
    project = await get_project_or_404(session, project_id)
    allowlist = Allowlist.for_project(project)

    results: list[McpServerToolsRead] = []
    for entry in project.mcp_servers or []:
        try:
            config = McpServerConfig.from_mapping(entry)
        except (ValueError, TypeError) as exc:
            results.append(McpServerToolsRead(server=str(entry.get("name", "?")), tools=[], error=str(exc)))
            continue
        try:
            async with connect_server(config, allowlist) as client:
                discovered = await client.discover()
        except Exception as exc:  # an unreachable server is reported per server
            results.append(McpServerToolsRead(server=config.name, tools=[], error=str(exc)))
            continue
        results.append(
            McpServerToolsRead(
                server=config.name,
                tools=[
                    McpToolRead(
                        name=t.name,
                        description=t.description,
                        input_schema=t.input_schema,
                        allowed=t.allowed,
                    )
                    for t in discovered
                ],
            )
        )
    return results
```

Register in `main.py`: add `mcp` to the router import tuple and `app.include_router(mcp.router)`.

- [ ] **Step 5: Run the tests; lint; types**

Run: `uv run pytest tests/api/test_mcp.py -q`, `uv run ruff check src tests/api/test_mcp.py`, `uv run mypy src`
Expected: PASS; clean.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/m7.txt <<'EOF'
feat(mcp): discovery route and a typed server list

GET /api/projects/{id}/mcp/tools connects to each configured server and
lists its tools, each with an allowed flag from the project's allowlist --
how a user decides what to permit, and listing never permits. An
unreachable server is reported per server, not fatal. mcp_servers is now
validated as McpServerConfig on update (422 on a bad entry) while reads
stay lenient, the validation_tier pattern.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/schemas/mcp.py backend/src/econometrica/api/routers/mcp.py \
  backend/src/econometrica/schemas/project.py backend/src/econometrica/main.py \
  backend/tests/api/test_mcp.py
git commit -F /tmp/m7.txt && rm -f /tmp/m7.txt
```

---

## Task 8: Record MCP is wired

**Files:** `CLAUDE.md`, `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Update the paragraph that now reads "`mcp/` is now the one thing still imported only by its own tests": MCP is wired end to end — a `researcher` role runs a bounded tool-calling loop over a project's allowlisted MCP tools before planning, gathering context (never a number) into the Planner's prompt; the connection layer (`mcp/config.py`, `mcp/connect.py`) holds the SDK transports; a discovery route builds the allowlist. State the **stdio trust model** in one line (an unsandboxed local command; HTTP for anything less than fully trusted). Note the `researcher` CHECK migration. Point at `docs/plans/2026-07-31-econometrica-mcp-{design,implementation}.md`. Keep the MCP allowlist paragraph (default-deny, exact match) — it still holds.

- [ ] **Step 2: Update README.md**

Add a sentence to the context section: a run can also call a project's configured MCP tools (a research phase before planning), gated by an explicit per-tool allowlist, and — like the web and documents — nothing a tool returns becomes a number.

- [ ] **Step 3: Commit**

```bash
cat > /tmp/m8.txt <<'EOF'
docs: record MCP tools are wired as a research phase

mcp/ was imported only by its own tests: no server schema, no connection
layer, nothing deciding when a tool gets called. All closed -- a typed
config, stdio/http transports, a researcher agent running a bounded
tool-calling loop before planning, and a discovery route to build the
allowlist. Records the context-not-numbers framing, the stdio
trust-the-command model, and the researcher CHECK migration.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add CLAUDE.md README.md
git commit -F /tmp/m8.txt && rm -f /tmp/m8.txt
```

---

## Final verification

From `backend/` (Postgres up):

- [ ] `uv run pytest -q` — full suite green (existing + new; live/subprocess tests included).
- [ ] `uv run ruff check src tests alembic` — clean.
- [ ] `uv run mypy src` — clean.
- [ ] `uv run alembic upgrade head && uv run alembic check` — applies the `researcher` revision, no drift.

---

## Self-review notes

- **Spec coverage.** Typed config (T1) ↔ "the connection layer / McpServerConfig". Transports + degradation (T2) ↔ "the connection layer". The bounded loop, allowlist-only offering, error-feedback, cap-then-summarise (T3) ↔ "the Research agent and its loop". `researcher` trace agent + migration + repointed McpCall (T4) ↔ "trace and the migration". Orchestrator `_research_context`, Planner-only, degrade, order-first (T5) ↔ "the seam". Gating on capability+servers+allowlist+role+tool-calling (T6) ↔ "gating". Discovery route + typed update (T7) ↔ "discovery". Docs (T8) ↔ the CLAUDE.md item. The grounding invariant test and a live probe are named in the design's testing section; the grounding test belongs with the orchestrator/researcher tests (add one asserting an MCP-sourced number stays ungrounded) and the live probe is optional given the real-subprocess stdio test in T2/T3.
- **Type consistency.** `McpServerConfig.from_mapping(dict) -> McpServerConfig`; `connect_server(config, allowlist) -> AsyncCM[McpClient]`; `McpConnector(configs, allowlist).open() -> AsyncCM[list[McpClient]]`; `Researcher(provider, model, connector, *, max_rounds).research(question) -> ResearchOutcome`; `Orchestrator(..., researcher: Researcher | None)`. `agent="researcher"` matches the widened `STEP_AGENTS`.
- **No placeholders.** Every code and test block is complete. Two spots are flagged for the implementer to resolve against the real toolchain, not left vague: the `Connector` protocol's return-type annotation (mypy may want `AbstractAsyncContextManager`), and `_supports_tools` being `async` and awaited inside the async `_build` (the `run_sync` shim is explicitly retracted). The stdio round-trip test spawns a real subprocess — deliberate, and called out.
- **Scope.** Eight tasks, the largest of the three features, but each ends in an independently testable deliverable and the spine is strictly sequential (config → connect → loop → migration → orchestrator → router → route → docs).
