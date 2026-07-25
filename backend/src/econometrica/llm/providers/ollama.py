"""Ollama adapter — local models, no API key.

Ollama's chat API is close to OpenAI's but differs in three ways that matter
here: it streams newline-delimited JSON rather than SSE, it reports token
counts as ``prompt_eval_count``/``eval_count``, and its tool calls carry no
correlation id, so this adapter synthesises them.
"""

import asyncio
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
#:
#: Only a fallback since the adapter started asking ``/api/show``, which
#: reports capabilities outright. Kept because a daemon too old to answer that
#: — or one erroring on a single model — must still produce a usable listing,
#: and because these heuristics were right about every model on this machine.
#: They are still heuristics: "bge-reranker-chat" would be a victim.
_EMBEDDING_FAMILIES = frozenset({"bert", "nomic-bert", "gemma-embed", "xlm-roberta"})
_EMBEDDING_NAME_HINTS = ("embed", "minilm", "bge-")

#: Used only when the daemon will not say. Ollama's own default num_ctx is
#: 4096; 8192 is the more common model default, and either way this is a guess
#: that the ``/api/show`` path exists to avoid.
DEFAULT_CONTEXT_WINDOW = 8192


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
        entries: list[dict[str, Any]] = list(payload.get("models", []))

        # ``/api/tags`` reports neither context length nor tool support, and
        # inferring them from the model's name was wrong in both directions:
        # every model claimed an 8192 window when this machine serves 2048 to
        # 262144, and several that complete cannot call tools. ``/api/show``
        # states both. It costs one request per model — 16 of them
        # concurrently against a local daemon measured at 0.25s.
        described = await asyncio.gather(
            *(self._describe(_entry_id(entry)) for entry in entries)
        )

        return [
            self._model_info(entry, detail)
            for entry, detail in zip(entries, described, strict=True)
        ]

    async def _describe(self, model_id: str) -> dict[str, Any] | None:
        """What the daemon knows about one model, or None if it will not say.

        Never raises. A model the daemon cannot describe should still appear
        in the listing under the tags heuristic — the alternative is one bad
        model emptying the picker.
        """
        if not model_id:
            return None
        try:
            response = await self._client.post("/api/show", json={"model": model_id})
            if response.status_code >= 400:
                return None
            return dict(response.json())
        except (httpx.HTTPError, ValueError):
            return None

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

    def _model_info(
        self, entry: dict[str, Any], detail: dict[str, Any] | None
    ) -> ModelInfo:
        model_id = _entry_id(entry)
        reported = detail.get("capabilities") if detail else None
        capabilities = (
            _described_capabilities(set(reported), detail)
            if isinstance(reported, list)
            else _guessed_capabilities(entry, model_id)
        )
        return ModelInfo(id=model_id, name=model_id, capabilities=capabilities)

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


def _entry_id(entry: dict[str, Any]) -> str:
    value = entry.get("name") or entry.get("model", "")
    return str(value)


def _described_capabilities(
    reported: set[str], detail: dict[str, Any] | None
) -> Capabilities:
    """Capabilities as the daemon states them."""
    chat = "completion" in reported
    return Capabilities(
        tool_calling="tools" in reported,
        # Not in the reported list, and correctly so: `format: json` is a
        # decoding constraint the daemon applies to any model that completes,
        # not a property of the model.
        json_mode=chat,
        streaming=chat,
        vision="vision" in reported,
        context_window=_context_length(detail) or DEFAULT_CONTEXT_WINDOW,
    )


def _guessed_capabilities(entry: dict[str, Any], model_id: str) -> Capabilities:
    """Fallback for a daemon that will not describe the model."""
    family = str(entry.get("details", {}).get("family", "")).lower()
    embedding = family in _EMBEDDING_FAMILIES or any(
        hint in model_id.lower() for hint in _EMBEDDING_NAME_HINTS
    )
    return Capabilities(
        tool_calling=not embedding,
        json_mode=not embedding,
        streaming=not embedding,
        context_window=DEFAULT_CONTEXT_WINDOW,
    )


def _context_length(detail: dict[str, Any] | None) -> int | None:
    """The model's own window, under its architecture-prefixed key.

    The key is named for the architecture (``qwen3moe.context_length``), so
    ``general.architecture`` names it exactly. Matching on the suffix alone
    would be ambiguous: ministral-3 also reports
    ``mistral3.rope.scaling.original_context_length``, a *different and
    smaller* number, and picking it would silently understate the window.
    """
    info: dict[str, Any] = (detail or {}).get("model_info", {})

    architecture = info.get("general.architecture")
    if isinstance(architecture, str):
        value = info.get(f"{architecture}.context_length")
        if isinstance(value, int):
            return value

    # No architecture reported: take the unqualified key, which is the one
    # with nothing between the prefix and the suffix.
    for key, value in info.items():
        if key.endswith(".context_length") and key.count(".") == 1 and isinstance(value, int):
            return value
    return None


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
