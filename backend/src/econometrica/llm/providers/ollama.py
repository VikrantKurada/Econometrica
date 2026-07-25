"""Ollama adapter — local models, no API key.

Ollama's chat API is close to OpenAI's but differs in three ways that matter
here: it streams newline-delimited JSON rather than SSE, it reports token
counts as ``prompt_eval_count``/``eval_count``, and its tool calls carry no
correlation id, so this adapter synthesises them.
"""

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from econometrica.llm.errors import (
    ModelNotFoundError,
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

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT = 300.0

_UNREACHABLE_HINT = (
    "cannot reach the Ollama daemon at {url} ({detail}). Start it with "
    "`ollama serve`, or point OLLAMA_BASE_URL at a running instance."
)

#: Model families that only produce embeddings. Offering one as a chat model
#: yields a confusing runtime failure, so they are flagged at listing time.
_EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert", "gemma-embed", "xlm-roberta"})
_EMBEDDING_NAME_HINTS = ("embed", "minilm", "bge-")


class OllamaProvider:
    """Chat completions against a local Ollama daemon."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- models -------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        payload = await self._get_json("/api/tags")
        return [self._model_info(entry) for entry in payload.get("models", [])]

    async def health(self) -> ProviderHealth:
        """Never raises: the providers endpoint reports status, it does not fail."""
        try:
            models = await self.list_models()
        except ProviderUnavailableError as exc:
            return ProviderHealth(provider=self.name, reachable=False, detail=exc.detail)
        except Exception as exc:
            # Deliberately broad: health backs a status endpoint, and an
            # unexpected failure is information to report, not to propagate.
            return ProviderHealth(provider=self.name, reachable=False, detail=str(exc))
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
        body = self._request_body(
            messages, model, tools, temperature, max_tokens, stream=False
        )
        if json_mode:
            body["format"] = "json"

        started = _now()
        payload = await self._post_json("/api/chat", body, model=model)
        latency = (_now() - started) * 1000.0

        message = payload.get("message", {})
        calls = _tool_calls(message.get("tool_calls", []))
        return Completion(
            content=message.get("content", ""),
            tool_calls=calls,
            usage=Usage(
                input_tokens=int(payload.get("prompt_eval_count", 0)),
                output_tokens=int(payload.get("eval_count", 0)),
            ),
            model=payload.get("model", model),
            provider=self.name,
            stop_reason="tool_use" if calls else payload.get("done_reason"),
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
        body = self._request_body(
            messages, model, tools, temperature, max_tokens, stream=True
        )
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as response:
                await self._raise_for_status(response, model, streaming=True)
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue  # Ollama emits a trailing blank line.
                    chunk = self._stream_chunk(line)
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    # --- internals ----------------------------------------------------------

    def _request_body(
        self,
        messages: Sequence[Message],
        model: str,
        tools: Sequence[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens

        body: dict[str, Any] = {
            "model": model,
            "messages": [_wire_message(m) for m in messages],
            "stream": stream,
            "options": options,
        }
        if tools:
            body["tools"] = [_wire_tool(t) for t in tools]
        return body

    def _stream_chunk(self, line: str) -> StreamChunk | None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderResponseError(
                self.name, f"malformed stream line: {line[:120]!r}"
            ) from exc

        message = payload.get("message", {})
        if not payload.get("done"):
            delta = message.get("content", "")
            return StreamChunk(delta=delta) if delta else None

        calls = _tool_calls(message.get("tool_calls", []))
        return StreamChunk(
            delta=message.get("content", ""),
            done=True,
            tool_calls=calls,
            usage=Usage(
                input_tokens=int(payload.get("prompt_eval_count", 0)),
                output_tokens=int(payload.get("eval_count", 0)),
            ),
            stop_reason="tool_use" if calls else payload.get("done_reason"),
        )

    def _model_info(self, entry: dict[str, Any]) -> ModelInfo:
        model_id = entry.get("name") or entry.get("model", "")
        family = str(entry.get("details", {}).get("family", "")).lower()
        embedding = family in _EMBEDDING_FAMILIES or any(
            hint in model_id.lower() for hint in _EMBEDDING_NAME_HINTS
        )
        return ModelInfo(
            id=model_id,
            name=model_id,
            capabilities=Capabilities(
                # Ollama exposes tool calling generically; whether a given model
                # honours it depends on its template, which tags does not report.
                tool_calling=not embedding,
                json_mode=not embedding,
                streaming=not embedding,
                context_window=8192,
            ),
        )

    async def _get_json(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path)
            await self._raise_for_status(response, model=None)
            return dict(response.json())
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

    async def _post_json(
        self, path: str, body: dict[str, Any], *, model: str
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body)
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
        detail = response.text[:300]
        if response.status_code == 404 and model:
            raise ModelNotFoundError(self.name, model)
        raise ProviderResponseError(
            self.name, f"HTTP {response.status_code} from {response.request.url}: {detail}"
        )

    def _unreachable(self, exc: httpx.HTTPError) -> ProviderUnavailableError:
        return ProviderUnavailableError(
            self.name, _UNREACHABLE_HINT.format(url=self.base_url, detail=exc)
        )


# --- wire helpers -----------------------------------------------------------


def _wire_message(message: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role.value, "content": message.content}
    if message.role is Role.ASSISTANT and message.tool_calls:
        wire["tool_calls"] = [
            {"function": {"name": c.name, "arguments": c.arguments}}
            for c in message.tool_calls
        ]
    return wire


def _wire_tool(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.input_schema,
        },
    }


def _tool_calls(raw: list[dict[str, Any]]) -> list[ToolCall]:
    """Ollama issues no correlation ids, so synthesise stable ones."""
    calls = []
    for entry in raw:
        function = entry.get("function", {})
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            # Some model templates emit the arguments as a JSON string.
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        calls.append(
            ToolCall(
                id=entry.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                name=function.get("name", ""),
                arguments=dict(arguments),
            )
        )
    return calls


def _now() -> float:
    import time

    return time.perf_counter()
