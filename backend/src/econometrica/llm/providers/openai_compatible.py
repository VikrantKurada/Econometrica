"""Shared transport for providers speaking the OpenAI chat-completions API.

OpenAI and NVIDIA NIM differ only in host, credential and name, so the wire
handling lives here once. Anything genuinely vendor-specific belongs in the
subclass, not in a flag on this class.

Two details this handles that a naive implementation gets wrong:

* ``content`` is ``null`` — not absent — on a tool-call turn, so it must be
  normalised to an empty string.
* Streamed tool calls arrive split across deltas: the id and name in one
  chunk, the arguments dribbled across several more. They are accumulated by
  index and only parsed once the turn ends.
"""

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

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

DEFAULT_TIMEOUT = 300.0


class OpenAICompatibleProvider:
    """Base adapter for the OpenAI chat-completions protocol."""

    name = "openai-compatible"
    base_url = ""
    default_context_window = 128_000

    def __init__(
        self,
        api_key: str = "",
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        if base_url is not None:
            self.base_url = base_url
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- models -------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        self._require_key()
        payload = await self._request_json("GET", "/models")
        return [
            ModelInfo(
                id=entry.get("id", ""),
                name=entry.get("id", ""),
                capabilities=Capabilities(
                    tool_calling=True,
                    json_mode=True,
                    streaming=True,
                    context_window=self.default_context_window,
                ),
            )
            for entry in payload.get("data", [])
        ]

    async def health(self) -> ProviderHealth:
        """Never raises: this backs a status endpoint."""
        if not self.api_key:
            return ProviderHealth(
                provider=self.name,
                reachable=False,
                detail=f"no api key configured for {self.name}",
            )
        try:
            models = await self.list_models()
        except Exception as exc:
            # Deliberately broad: an unexpected failure here is information to
            # report, not to propagate into a status page.
            detail = exc.detail if hasattr(exc, "detail") else str(exc)
            return ProviderHealth(provider=self.name, reachable=False, detail=str(detail))
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
        body = self._body(messages, model, tools, temperature, max_tokens, stream=False)
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        payload = await self._request_json("POST", "/chat/completions", body, model=model)
        latency = (time.perf_counter() - started) * 1000.0

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message", {})
        calls = _parse_tool_calls(message.get("tool_calls") or [])
        usage = payload.get("usage") or {}

        return Completion(
            # `content` is null on tool-call turns, not absent.
            content=message.get("content") or "",
            tool_calls=calls,
            usage=Usage(
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            model=payload.get("model", model),
            provider=self.name,
            stop_reason="tool_use" if calls else choice.get("finish_reason"),
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
        body = self._body(messages, model, tools, temperature, max_tokens, stream=True)
        # Without this, a streamed turn reports no token counts at all, and the
        # telemetry layer silently records zero cost for every streamed reply.
        body["stream_options"] = {"include_usage": True}

        pending: dict[int, dict[str, Any]] = {}
        usage = Usage()
        finish: str | None = None

        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=body, headers=self._headers()
            ) as response:
                await self._raise_for_status(response, model, streaming=True)
                async for line in response.aiter_lines():
                    event = _sse_payload(line)
                    if event is None:
                        continue

                    if event.get("usage"):
                        usage = Usage(
                            input_tokens=int(event["usage"].get("prompt_tokens", 0)),
                            output_tokens=int(event["usage"].get("completion_tokens", 0)),
                        )

                    choice = (event.get("choices") or [{}])[0]
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta") or {}

                    _accumulate_tool_calls(pending, delta.get("tool_calls") or [])

                    if delta.get("content"):
                        yield StreamChunk(delta=delta["content"])
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

        calls = _finish_tool_calls(pending)
        yield StreamChunk(
            done=True,
            tool_calls=calls,
            usage=usage,
            stop_reason="tool_use" if calls else finish,
        )

    # --- internals ----------------------------------------------------------

    def _require_key(self) -> None:
        if not self.api_key:
            raise ProviderAuthError(
                self.name,
                f"no api key configured for {self.name}; add one in provider settings",
            )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _body(
        self,
        messages: Sequence[Message],
        model: str,
        tools: Sequence[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [_wire_message(m) for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        return body

    async def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, json=body, headers=self._headers()
            )
            await self._raise_for_status(response, model)
            return dict(response.json())
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    async def _raise_for_status(
        self, response: httpx.Response, model: str | None, *, streaming: bool = False
    ) -> None:
        if response.status_code < 400:
            return
        if streaming:
            await response.aread()

        detail = self._redact(_error_message(response))
        status = response.status_code

        if status in (401, 403):
            raise ProviderAuthError(self.name, detail)
        if status == 429:
            raise ProviderRateLimitError(self.name, detail, _retry_after(response))
        if status == 404 and model:
            raise ModelNotFoundError(self.name, model)
        if status >= 500:
            raise ProviderUnavailableError(self.name, f"HTTP {status}: {detail}")
        raise ProviderResponseError(self.name, f"HTTP {status}: {detail}")

    def _redact(self, text: str) -> str:
        """Providers echo the offending key back in auth errors.

        Those errors reach logs and the UI, so the key must not travel with
        them.
        """
        return text.replace(self.api_key, "***") if self.api_key else text

    def _unreachable(self, exc: httpx.HTTPError) -> ProviderUnavailableError:
        return ProviderUnavailableError(
            self.name, f"cannot reach {self.base_url} ({self._redact(str(exc))})"
        )


# --- wire helpers -----------------------------------------------------------


def _wire_message(message: Message) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "content": message.content,
            "tool_call_id": message.tool_call_id,
        }

    wire: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.role is Role.ASSISTANT and message.tool_calls:
        wire["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in message.tool_calls
        ]
    return wire


def _parse_arguments(raw: str) -> dict[str, Any]:
    """Keep malformed arguments rather than dropping them.

    A model that emits broken JSON is a real failure mode; discarding the text
    hides why the tool call could not be executed.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


def _parse_tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    return [
        ToolCall(
            id=entry.get("id", ""),
            name=entry.get("function", {}).get("name", ""),
            arguments=_parse_arguments(entry.get("function", {}).get("arguments", "")),
        )
        for entry in raw
    ]


def _accumulate_tool_calls(
    pending: dict[int, dict[str, Any]], deltas: list[dict[str, Any]]
) -> None:
    """Merge streamed tool-call fragments, keyed by their index."""
    for delta in deltas:
        slot = pending.setdefault(
            int(delta.get("index", 0)), {"id": "", "name": "", "arguments": ""}
        )
        if delta.get("id"):
            slot["id"] = delta["id"]
        function = delta.get("function") or {}
        if function.get("name"):
            slot["name"] = function["name"]
        if function.get("arguments"):
            slot["arguments"] += function["arguments"]


def _finish_tool_calls(pending: dict[int, dict[str, Any]]) -> list[ToolCall]:
    return [
        ToolCall(
            id=slot["id"] or f"call_{index}",
            name=slot["name"],
            arguments=_parse_arguments(slot["arguments"]),
        )
        for index, slot in sorted(pending.items())
    ]


def _sse_payload(line: str) -> dict[str, Any] | None:
    """Decode one SSE line, skipping keep-alives, terminators and junk.

    A malformed event mid-stream should not destroy an otherwise good reply,
    so undecodable lines are skipped rather than raised.
    """
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    return str(error or body)[:300]


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
