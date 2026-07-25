"""A scriptable provider for tests.

This is the reference implementation of the protocol and the spy the Phase 4
agent tests will run against: it records every call, so a test can assert what
an agent actually asked the model, not merely what it did with the answer.

Deliberately in ``src`` rather than ``tests``: the agent, orchestrator and API
test suites all need it, and a shared fixture that lives in one suite's tests
directory becomes an import tangle.
"""

import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from econometrica.llm.errors import ModelNotFoundError, ProviderError, ProviderUnavailableError
from econometrica.llm.types import (
    Capabilities,
    Completion,
    Message,
    ModelInfo,
    ProviderHealth,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)

DEFAULT_MODELS = ("fake-1", "fake-2")


@dataclass
class RecordedCall:
    messages: list[Message]
    model: str
    tools: list[ToolSpec] | None
    temperature: float
    max_tokens: int | None
    json_mode: bool
    streamed: bool = False


@dataclass
class FakeProvider:
    """A provider that returns what you tell it to.

    ``responses`` and ``tool_calls`` are consumed in order; whichever is
    supplied drives the reply. Running past the end raises rather than looping,
    so a test that triggers more model calls than it intended fails loudly
    instead of silently passing on a stale answer.
    """

    name: str = "fake"
    responses: list[str] = field(default_factory=list)
    tool_calls: list[list[ToolCall]] = field(default_factory=list)
    #: ``None`` accepts any model id. Set it only when the test is *about*
    #: model validation — most tests should not have to know magic names.
    known_models: Sequence[str] | None = None
    reachable: bool = True
    error: ProviderError | None = None
    calls: list[RecordedCall] = field(default_factory=list)
    _cursor: int = 0

    async def list_models(self) -> list[ModelInfo]:
        if not self.reachable:
            raise ProviderUnavailableError(self.name, "fake provider marked unreachable")
        return [
            ModelInfo(
                id=model,
                name=model,
                capabilities=Capabilities(
                    tool_calling=True, json_mode=True, streaming=True, context_window=128_000
                ),
            )
            for model in (self.known_models or DEFAULT_MODELS)
        ]

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> Completion:
        started = time.perf_counter()
        self._guard(model)
        self.calls.append(
            RecordedCall(
                messages=list(messages),
                model=model,
                tools=list(tools) if tools is not None else None,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        )
        content, calls = self._next()
        return Completion(
            content=content,
            tool_calls=calls,
            usage=self._usage(messages, content),
            model=model,
            provider=self.name,
            stop_reason="tool_use" if calls else "end_turn",
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        self._guard(model)
        self.calls.append(
            RecordedCall(
                messages=list(messages),
                model=model,
                tools=list(tools) if tools is not None else None,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=False,
                streamed=True,
            )
        )
        content, calls = self._next()

        # Word-at-a-time, preserving separators, so reassembly is exact.
        pieces = content.split(" ")
        for i, piece in enumerate(pieces):
            yield StreamChunk(delta=piece if i == 0 else f" {piece}")
        yield StreamChunk(
            delta="",
            done=True,
            tool_calls=calls,
            usage=self._usage(messages, content),
            stop_reason="tool_use" if calls else "end_turn",
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            reachable=self.reachable,
            detail="" if self.reachable else "fake provider marked unreachable",
            models_available=(
                len(self.known_models or DEFAULT_MODELS) if self.reachable else 0
            ),
        )

    # --- internals ----------------------------------------------------------

    def _guard(self, model: str) -> None:
        if self.error is not None:
            raise self.error
        if not self.reachable:
            raise ProviderUnavailableError(self.name, "fake provider marked unreachable")
        if self.known_models is not None and model not in self.known_models:
            raise ModelNotFoundError(self.name, model)

    def _next(self) -> tuple[str, list[ToolCall]]:
        index = self._cursor
        self._cursor += 1
        if index < len(self.tool_calls):
            return "", self.tool_calls[index]
        if index < len(self.responses):
            return self.responses[index], []
        raise AssertionError(
            f"{self.name}: ran out of scripted responses on call {index + 1}; "
            f"script had {len(self.responses)} response(s) and "
            f"{len(self.tool_calls)} tool-call turn(s)"
        )

    @staticmethod
    def _usage(messages: Sequence[Message], content: str) -> Usage:
        """A crude but monotone token proxy — enough to test accounting paths."""
        prompt = sum(len(m.content.split()) for m in messages)
        return Usage(input_tokens=max(1, prompt), output_tokens=max(1, len(content.split())))
