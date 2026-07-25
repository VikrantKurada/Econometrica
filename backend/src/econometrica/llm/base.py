"""The single interface every provider adapter implements.

Runtime-checkable so tests can assert conformance, but the real contract is
behavioural and lives in ``tests/llm/test_base.py``: any adapter added later
should be exercised against those same expectations.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Protocol, runtime_checkable

from econometrica.llm.types import (
    Completion,
    Message,
    ModelInfo,
    ProviderHealth,
    StreamChunk,
    ToolSpec,
)


@runtime_checkable
class LLMProvider(Protocol):
    """A chat-completion provider.

    Implementations must raise only
    :class:`~econometrica.llm.errors.ProviderError` subclasses for failures the
    caller could act on — a bare ``httpx`` or SDK exception escaping an adapter
    is a bug in that adapter.
    """

    name: str

    async def list_models(self) -> list[ModelInfo]:
        """Models this provider can serve right now."""
        ...

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
        """Run one turn to completion."""
        ...

    def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Run one turn, yielding increments.

        Not ``async def``: implementations are async generators, so calling
        this returns the iterator directly rather than a coroutine that must
        first be awaited.
        """
        ...

    async def health(self) -> ProviderHealth:
        """Cheap reachability probe. Must not raise."""
        ...
