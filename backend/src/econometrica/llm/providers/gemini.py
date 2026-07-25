"""Google Gemini adapter.

Gemini's wire format diverges from the OpenAI shape more than any other
provider in this application, and the translation lives entirely here:

* ``contents``/``parts`` rather than ``messages``/``content``.
* The assistant role is called ``model``.
* The system prompt is its own ``systemInstruction`` field.
* Function calls carry no id, so ids are synthesised — and a function *result*
  is keyed back to its call by **name**, which the protocol does not carry.
  The adapter resolves the name by looking back through the conversation.
* Safety blocks arrive as a successful response with an empty candidate, or
  with no candidates at all. Both are normalised to a ``refusal`` stop reason
  so callers do not index into an empty parts list.
"""

import json
import time
import uuid
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

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_TIMEOUT = 300.0

#: Gemini's finishReason vocabulary mapped onto the shared one. SAFETY and
#: PROHIBITED_CONTENT become ``refusal`` so a policy block reads the same here
#: as it does on Anthropic.
_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "BLOCKLIST": "refusal",
    "SPII": "refusal",
    "RECITATION": "recitation",
    "MALFORMED_FUNCTION_CALL": "malformed_function_call",
    "OTHER": "other",
}

#: A model that cannot `generateContent` is an embedding model; offering it as
#: a chat model produces a confusing runtime failure.
_CHAT_METHOD = "generateContent"


class GeminiProvider:
    """Chat completions against the Gemini generative language API."""

    name = "gemini"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # --- models -------------------------------------------------------------

    async def list_models(self) -> list[ModelInfo]:
        self._require_key()
        payload = await self._request_json("GET", "/models", params={"pageSize": 200})

        models: list[ModelInfo] = []
        for entry in payload.get("models", []):
            methods = entry.get("supportedGenerationMethods", [])
            if _CHAT_METHOD not in methods:
                continue
            # The API returns "models/gemini-2.5-pro"; the id callers use is the
            # bare name, and sending the prefixed form back is an error.
            model_id = str(entry.get("name", "")).removeprefix("models/")
            models.append(
                ModelInfo(
                    id=model_id,
                    name=entry.get("displayName") or model_id,
                    capabilities=Capabilities(
                        tool_calling=True,
                        json_mode=True,
                        streaming=True,
                        vision=True,
                        context_window=int(entry.get("inputTokenLimit") or 32_768),
                    ),
                )
            )
        return models

    async def health(self) -> ProviderHealth:
        """Never raises: this backs a status endpoint."""
        if not self.api_key:
            return ProviderHealth(
                provider=self.name,
                reachable=False,
                detail="no api key configured for gemini",
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
        body = self._body(messages, tools, temperature, max_tokens, json_mode)

        started = time.perf_counter()
        payload = await self._request_json(
            "POST", f"/models/{model}:generateContent", body=body, model=model
        )
        latency = (time.perf_counter() - started) * 1000.0

        text, calls, finish = _read_candidate(payload)
        return Completion(
            content=text,
            tool_calls=calls,
            usage=_usage(payload.get("usageMetadata")),
            model=payload.get("modelVersion") or model,
            provider=self.name,
            stop_reason="tool_use" if calls else finish,
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
        body = self._body(messages, tools, temperature, max_tokens, json_mode=False)

        calls: list[ToolCall] = []
        usage = Usage()
        finish: str | None = None

        try:
            async with self._client.stream(
                "POST",
                f"/models/{model}:streamGenerateContent",
                # Without alt=sse the API streams a single JSON array rather
                # than server-sent events, and nothing arrives until the end.
                params={"alt": "sse"},
                json=body,
                headers=self._headers(),
            ) as response:
                await self._raise_for_status(response, model, streaming=True)
                async for line in response.aiter_lines():
                    event = _sse_payload(line)
                    if event is None:
                        continue
                    text, event_calls, event_finish = _read_candidate(event)
                    calls.extend(event_calls)
                    finish = event_finish or finish
                    if event.get("usageMetadata"):
                        usage = _usage(event["usageMetadata"])
                    if text:
                        yield StreamChunk(delta=text)
        except httpx.HTTPError as exc:
            raise self._unreachable(exc) from exc

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
                self.name, "no api key configured for gemini; add one in settings"
            )

    def _headers(self) -> dict[str, str]:
        # The key goes in a header rather than the documented `?key=` query
        # parameter: query strings land in proxy and server access logs.
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _body(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] | None,
        temperature: float,
        max_tokens: int | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        system, contents = self._split(messages)

        config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            config["maxOutputTokens"] = max_tokens
        if json_mode:
            config["responseMimeType"] = "application/json"

        body: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            body["systemInstruction"] = {"parts": system}
        if tools:
            body["tools"] = [{"functionDeclarations": [_declaration(t) for t in tools]}]
        return body

    def _split(
        self, messages: Sequence[Message]
    ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        system: list[dict[str, str]] = []
        contents: list[dict[str, Any]] = []

        # Gemini matches a function result to its call by name, but the
        # protocol only carries the call id, so build the mapping as we go.
        names_by_call_id = {
            call.id: call.name
            for message in messages
            for call in message.tool_calls
        }

        for message in messages:
            if message.role is Role.SYSTEM:
                system.append({"text": message.content})
                continue

            if message.role is Role.TOOL:
                name = names_by_call_id.get(message.tool_call_id or "")
                if name is None:
                    raise ProviderResponseError(
                        self.name,
                        f"orphan tool result {message.tool_call_id!r}: no matching "
                        "function call in the conversation, and Gemini matches "
                        "results to calls by name",
                    )
                part = {
                    "functionResponse": {
                        "name": name,
                        "response": _response_object(message.content),
                    }
                }
                if contents and contents[-1]["role"] == "user" and _is_function_turn(
                    contents[-1]
                ):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part]})
                continue

            if message.role is Role.ASSISTANT and message.tool_calls:
                parts: list[dict[str, Any]] = []
                if message.content:
                    parts.append({"text": message.content})
                parts.extend(
                    {"functionCall": {"name": c.name, "args": c.arguments}}
                    for c in message.tool_calls
                )
                contents.append({"role": "model", "parts": parts})
                continue

            role = "model" if message.role is Role.ASSISTANT else "user"
            contents.append({"role": role, "parts": [{"text": message.content}]})

        return system, contents

    async def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        *,
        model: str | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(
                method, path, json=body, params=params, headers=self._headers()
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

        error = _error_body(response)
        detail = self._redact(str(error.get("message") or response.text[:300]))
        status = str(error.get("status") or "")
        code = response.status_code

        if status in ("UNAUTHENTICATED", "PERMISSION_DENIED") or _is_key_error(detail):
            # An invalid key comes back as 400 INVALID_ARGUMENT, not 401, so the
            # HTTP status alone would misclassify it as a bad request.
            raise ProviderAuthError(self.name, detail)
        if status == "RESOURCE_EXHAUSTED" or code == 429:
            raise ProviderRateLimitError(self.name, detail, _retry_after(response))
        if code == 404 and model:
            raise ModelNotFoundError(self.name, model)
        if code >= 500:
            raise ProviderUnavailableError(self.name, f"HTTP {code}: {detail}")
        raise ProviderResponseError(self.name, f"HTTP {code}: {detail}")

    def _redact(self, text: str) -> str:
        return text.replace(self.api_key, "***") if self.api_key else text

    def _unreachable(self, exc: httpx.HTTPError) -> ProviderUnavailableError:
        return ProviderUnavailableError(
            self.name, f"cannot reach {self.base_url} ({self._redact(str(exc))})"
        )


# --- wire helpers -----------------------------------------------------------


def _declaration(tool: ToolSpec) -> dict[str, Any]:
    declaration: dict[str, Any] = {"name": tool.name, "description": tool.description}
    # Gemini rejects a declaration whose parameters object has no properties,
    # so a no-argument tool must omit the field entirely.
    if tool.input_schema.get("properties"):
        declaration["parameters"] = tool.input_schema
    return declaration


def _read_candidate(payload: dict[str, Any]) -> tuple[str, list[ToolCall], str | None]:
    """Pull text, function calls and finish reason out of one response body.

    A safety block yields a candidate with no ``content``, or no candidates at
    all when the *prompt* was blocked. Both are reported as a refusal rather
    than an exception, so callers handle them like any other stop reason.
    """
    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        return "", [], "refusal" if blocked else None

    candidate = candidates[0]
    finish = _FINISH_REASONS.get(str(candidate.get("finishReason") or ""))

    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for part in (candidate.get("content") or {}).get("parts") or []:
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            function = part["functionCall"]
            calls.append(
                ToolCall(
                    # Gemini issues no correlation id; synthesise a stable one.
                    id=f"call_{uuid.uuid4().hex[:12]}",
                    name=function.get("name", ""),
                    arguments=dict(function.get("args") or {}),
                )
            )
    return "".join(text_parts), calls, finish


def _response_object(content: str) -> dict[str, Any]:
    """functionResponse.response must be an object; tools may return anything."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {"result": content}
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _is_function_turn(turn: dict[str, Any]) -> bool:
    parts = turn.get("parts") or []
    return bool(parts) and all("functionResponse" in p for p in parts)


def _usage(raw: dict[str, Any] | None) -> Usage:
    raw = raw or {}
    return Usage(
        input_tokens=int(raw.get("promptTokenCount") or 0),
        output_tokens=int(raw.get("candidatesTokenCount") or 0),
        cache_read_tokens=int(raw.get("cachedContentTokenCount") or 0),
    )


def _sse_payload(line: str) -> dict[str, Any] | None:
    """Decode one SSE line, skipping keep-alives and undecodable events."""
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _error_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, dict) else {}


def _is_key_error(detail: str) -> bool:
    lowered = detail.lower()
    return "api key not valid" in lowered or "api_key_invalid" in lowered


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
