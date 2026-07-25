"""Anthropic adapter, built on the official ``anthropic`` SDK.

This is the one adapter that does not speak raw HTTP. Anthropic ships a
first-party SDK, and using it rather than hand-rolling the wire format buys
correct handling of tool-use blocks, the streaming event protocol, retries and
beta headers — all of which are easy to get subtly wrong by hand.

Four things about this API differ from the OpenAI-shaped providers and are
handled here rather than pushed onto callers:

* The system prompt is its own request field, not a message role.
* Tool results ride as content blocks inside a *user* turn, and consecutive
  results must be merged into one turn or parallel tool use degrades.
* ``max_tokens`` is required, while the protocol treats it as optional.
* Several current models **reject** ``temperature`` outright with a 400. The
  protocol exposes temperature uniformly across five providers, so the adapter
  drops it for those models rather than letting a caller discover the
  limitation at run time.
"""

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic
from anthropic import AsyncAnthropic
from anthropic.types import (
    ContentBlockParam,
    MessageParam,
    TextBlockParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlockParam,
)

from econometrica.llm.errors import (
    ModelNotFoundError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from econometrica.llm.types import (
    Capabilities,
    Completion,
    Message,
    ModelInfo,
    ProviderHealth,
    Role,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)

#: Models that return a 400 if ``temperature``/``top_p``/``top_k`` is sent at
#: all. Sampling parameters were removed from the Opus 4.7 generation onward;
#: prompting is the supported way to steer these models.
NO_SAMPLING_PARAMS: frozenset[str] = frozenset(
    {
        "claude-fable-5",
        "claude-mythos-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-sonnet-5",
    }
)

#: The API requires max_tokens. Generous rather than tight: hitting the cap
#: truncates mid-thought and costs a whole retry.
DEFAULT_MAX_TOKENS = 16_000

DEFAULT_TIMEOUT = 600.0


class AnthropicProvider:
    """Chat completions against the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str = "",
        client: AsyncAnthropic | None = None,
        http_client: Any = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        cache_system_prompt: bool = True,
    ) -> None:
        self.api_key = api_key
        #: System prompts for agent roles are long and stable across a run,
        #: which is exactly the shape prompt caching pays off on.
        self.cache_system_prompt = cache_system_prompt
        self._client = client or AsyncAnthropic(
            api_key=api_key or "placeholder",
            http_client=http_client,
            timeout=timeout,
            max_retries=max_retries,
        )

    async def aclose(self) -> None:
        await self._client.close()

    # --- models -------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        self._require_key()
        try:
            page = await self._client.models.list(limit=100)
        except Exception as exc:
            raise self._translate(exc, model=None) from exc

        return [
            ModelInfo(
                id=entry.id,
                name=getattr(entry, "display_name", "") or entry.id,
                capabilities=self._capabilities(entry),
            )
            for entry in page.data
        ]

    async def health(self) -> ProviderHealth:
        """Never raises: this backs a status endpoint."""
        if not self.api_key:
            return ProviderHealth(
                provider=self.name,
                reachable=False,
                detail="no api key configured for anthropic",
            )
        try:
            models = await self.list_models()
        except Exception as exc:
            # Deliberately broad: an unexpected failure here is information to
            # report, not to propagate into a status page.
            detail = getattr(exc, "detail", None) or str(exc)
            return ProviderHealth(
                provider=self.name, reachable=False, detail=self._redact(str(detail))
            )
        return ProviderHealth(
            provider=self.name, reachable=True, models_available=len(models)
        )

    # --- completion ---------------------------------------------------------

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
        self._require_key()
        kwargs = self._request_kwargs(messages, model, tools, temperature, max_tokens)

        started = time.perf_counter()
        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:
            raise self._translate(exc, model=model) from exc
        latency = (time.perf_counter() - started) * 1000.0

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                # Anthropic returns tool input already parsed — unlike the
                # OpenAI shape, there is no JSON string to decode here.
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
                )

        return Completion(
            content="".join(text_parts),
            tool_calls=calls,
            usage=_usage(response.usage),
            model=response.model,
            provider=self.name,
            stop_reason=response.stop_reason,
            latency_ms=latency,
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
        self._require_key()
        kwargs = self._request_kwargs(messages, model, tools, temperature, max_tokens)

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and event.delta.type == "text_delta"
                    ):
                        yield StreamChunk(delta=event.delta.text)
                final = await stream.get_final_message()
        except Exception as exc:
            raise self._translate(exc, model=model) from exc

        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
            for b in final.content
            if b.type == "tool_use"
        ]
        yield StreamChunk(
            done=True,
            tool_calls=calls,
            usage=_usage(final.usage),
            stop_reason=final.stop_reason,
        )

    # --- internals ----------------------------------------------------------

    def _require_key(self) -> None:
        if not self.api_key:
            raise ProviderAuthError(
                self.name, "no api key configured for anthropic; add one in settings"
            )

    def _request_kwargs(
        self,
        messages: Sequence[Message],
        model: str,
        tools: Sequence[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        system, turns = self._split(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": turns,
            "max_tokens": max_tokens or DEFAULT_MAX_TOKENS,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                ToolParam(
                    name=t.name, description=t.description, input_schema=t.input_schema
                )
                for t in tools
            ]
        if model not in NO_SAMPLING_PARAMS:
            kwargs["temperature"] = temperature
        return kwargs

    def _split(
        self, messages: Sequence[Message]
    ) -> tuple[list[TextBlockParam], list[MessageParam]]:
        """Separate the system prompt from the conversation turns.

        Tool results are folded into the preceding user turn: Anthropic carries
        them as content blocks in a user message, and splitting parallel results
        across several turns teaches the model to stop issuing parallel calls.
        """
        system: list[TextBlockParam] = []
        turns: list[MessageParam] = []

        for message in messages:
            if message.role is Role.SYSTEM:
                system.append(TextBlockParam(type="text", text=message.content))
                continue

            if message.role is Role.TOOL:
                result = ToolResultBlockParam(
                    type="tool_result",
                    tool_use_id=message.tool_call_id or "",
                    content=message.content,
                )
                pending = _pending_tool_results(turns)
                if pending is not None:
                    pending.append(result)
                else:
                    turns.append(MessageParam(role="user", content=[result]))
                continue

            if message.role is Role.ASSISTANT and message.tool_calls:
                blocks: list[ContentBlockParam] = []
                if message.content:
                    blocks.append(TextBlockParam(type="text", text=message.content))
                blocks.extend(
                    ToolUseBlockParam(
                        type="tool_use",
                        id=call.id,
                        name=call.name,
                        input=call.arguments,
                    )
                    for call in message.tool_calls
                )
                turns.append(MessageParam(role="assistant", content=blocks))
                continue

            turns.append(
                MessageParam(
                    role="assistant" if message.role is Role.ASSISTANT else "user",
                    content=message.content,
                )
            )

        if system and self.cache_system_prompt:
            # One breakpoint on the last block: caching is a prefix match, so
            # it covers the tool definitions and every earlier system block too.
            system[-1]["cache_control"] = {"type": "ephemeral"}
        return system, turns

    @staticmethod
    def _capabilities(entry: Any) -> Capabilities:
        caps = getattr(entry, "capabilities", None)
        return Capabilities(
            # Every current Claude model supports tool calling and streaming;
            # the Models API reports the features that actually vary.
            tool_calling=True,
            streaming=True,
            json_mode=_supported(caps, "structured_outputs"),
            vision=_supported(caps, "image_input"),
            context_window=getattr(entry, "max_input_tokens", None) or 200_000,
        )

    def _redact(self, text: str) -> str:
        """Providers echo the offending key back in auth errors."""
        return text.replace(self.api_key, "***") if self.api_key else text

    def _translate(self, exc: Exception, *, model: str | None) -> Exception:
        """Map SDK exceptions onto the provider-neutral hierarchy."""
        if isinstance(exc, ProviderAuthError):
            return exc

        detail = self._redact(str(getattr(exc, "message", None) or exc))

        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            return ProviderAuthError(self.name, detail)
        if isinstance(exc, anthropic.RateLimitError):
            return ProviderRateLimitError(self.name, detail, _retry_after(exc))
        if isinstance(exc, anthropic.NotFoundError):
            return ModelNotFoundError(self.name, model or "unknown")
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderUnavailableError(self.name, f"cannot reach anthropic: {detail}")
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code >= 500:
                return ProviderUnavailableError(
                    self.name, f"HTTP {exc.status_code}: {detail}"
                )
            return ProviderResponseError(self.name, f"HTTP {exc.status_code}: {detail}")
        return ProviderResponseError(self.name, detail)


# --- helpers ----------------------------------------------------------------


def _pending_tool_results(turns: list[MessageParam]) -> list[Any] | None:
    """The open tool-result turn to append to, if the last turn is one.

    Consecutive tool results belong in a single user turn. Splitting them
    across turns is accepted by the API but teaches the model to stop issuing
    parallel tool calls, which is a silent capability regression.
    """
    if not turns or turns[-1]["role"] != "user":
        return None
    content = turns[-1].get("content")
    if not isinstance(content, list) or not content:
        return None
    if all(
        (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_result"
        for b in content
    ):
        return content
    return None


def _supported(caps: Any, key: str) -> bool:
    """Read one capability flag from the Models API response.

    The SDK types ``capabilities`` as a ``ModelCapabilities`` model, but newly
    added capabilities arrive as plain dicts until the SDK catches up. Handle
    both, since assuming either one alone silently reports every flag as False.
    """
    if caps is None:
        return False
    entry = caps.get(key) if isinstance(caps, dict) else getattr(caps, key, None)
    if entry is None:
        return False
    supported = (
        entry.get("supported") if isinstance(entry, dict) else getattr(entry, "supported", None)
    )
    return bool(supported)


def _usage(raw: Any) -> Usage:
    return Usage(
        input_tokens=getattr(raw, "input_tokens", 0) or 0,
        output_tokens=getattr(raw, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(raw, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(raw, "cache_creation_input_tokens", 0) or 0,
    )


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    raw = response.headers.get("retry-after") if response is not None else None
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
