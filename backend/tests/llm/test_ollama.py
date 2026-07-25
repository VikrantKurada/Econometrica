"""Tests for the Ollama adapter.

The unit tests drive a mocked HTTP transport, so they are fast, offline and
deterministic. Two live tests at the bottom run against a real daemon when one
is present — a mock can only prove the adapter matches what I *believe* the
wire format is, which is exactly the assumption worth checking.
"""

import json

import httpx
import pytest

from econometrica.llm.errors import ModelNotFoundError, ProviderUnavailableError
from econometrica.llm.providers.ollama import OllamaProvider
from econometrica.llm.types import Message, ToolSpec

TAGS_RESPONSE = {
    "models": [
        {
            "name": "llama3.2:latest",
            "model": "llama3.2:latest",
            "size": 2019393189,
            "details": {"family": "llama", "parameter_size": "3.2B"},
        },
        {
            "name": "nomic-embed-text:latest",
            "model": "nomic-embed-text:latest",
            "size": 274302450,
            "details": {"family": "nomic-bert", "parameter_size": "137M"},
        },
    ]
}

CHAT_RESPONSE = {
    "model": "llama3.2:latest",
    "message": {"role": "assistant", "content": "The beta is 1.3."},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 26,
    "eval_count": 8,
}


def _provider(handler) -> OllamaProvider:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://localhost:11434")
    return OllamaProvider(client=client)


def _json_handler(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


# --- model listing ----------------------------------------------------------


async def test_lists_locally_available_models():
    provider = _provider(_json_handler(TAGS_RESPONSE))
    models = await provider.list_models()
    assert [m.id for m in models] == ["llama3.2:latest", "nomic-embed-text:latest"]


async def test_embedding_models_are_marked_as_not_supporting_chat():
    """Offering an embedding model as a chat model produces a baffling failure."""
    provider = _provider(_json_handler(TAGS_RESPONSE))
    models = {m.id: m for m in await provider.list_models()}
    assert models["llama3.2:latest"].capabilities.streaming is True
    assert models["nomic-embed-text:latest"].capabilities.streaming is False


async def test_a_stopped_daemon_gives_an_actionable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderUnavailableError) as exc:
        await _provider(handler).list_models()

    assert "ollama" in str(exc.value)
    assert "ollama serve" in str(exc.value)


# --- completion -------------------------------------------------------------


async def test_complete_returns_content_and_usage():
    provider = _provider(_json_handler(CHAT_RESPONSE))
    result = await provider.complete([Message.user("beta?")], model="llama3.2:latest")

    assert result.content == "The beta is 1.3."
    assert result.provider == "ollama"
    assert result.model == "llama3.2:latest"
    assert result.usage.input_tokens == 26
    assert result.usage.output_tokens == 8
    assert result.stop_reason == "stop"


async def test_complete_sends_the_conversation_in_ollama_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=CHAT_RESPONSE)

    await _provider(handler).complete(
        [Message.system("be terse"), Message.user("beta?")],
        model="llama3.2:latest",
        temperature=0.3,
    )

    assert captured["model"] == "llama3.2:latest"
    assert captured["stream"] is False
    assert captured["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "beta?"},
    ]
    assert captured["options"]["temperature"] == 0.3


async def test_unknown_model_raises_model_not_found():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": 'model "ghost" not found'})

    with pytest.raises(ModelNotFoundError, match="ghost"):
        await _provider(handler).complete([Message.user("hi")], model="ghost")


# --- streaming --------------------------------------------------------------


def _ndjson_handler(lines):
    def handler(request: httpx.Request) -> httpx.Response:
        body = "\n".join(json.dumps(line) for line in lines)
        return httpx.Response(200, content=body.encode())

    return handler


async def test_stream_reassembles_into_the_full_message():
    lines = [
        {"message": {"content": "The "}, "done": False},
        {"message": {"content": "beta "}, "done": False},
        {"message": {"content": "is 1.3."}, "done": False},
        {
            "message": {"content": ""},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 26,
            "eval_count": 8,
        },
    ]
    provider = _provider(_ndjson_handler(lines))

    chunks = [c async for c in provider.stream([Message.user("beta?")], model="m")]

    assert "".join(c.delta for c in chunks) == "The beta is 1.3."
    assert chunks[-1].done is True
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.output_tokens == 8
    assert chunks[-1].stop_reason == "stop"


async def test_stream_requests_streaming_mode():
    captured = {}

    done_line = json.dumps({"message": {"content": "x"}, "done": True})

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=done_line.encode())

    async for _ in _provider(handler).stream([Message.user("hi")], model="m"):
        pass

    assert captured["stream"] is True


async def test_blank_lines_in_the_stream_are_ignored():
    """Ollama emits a trailing newline; a naive parser dies on the empty line."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"message": {"content": "hi"}, "done": False}) + "\n\n"
        body += json.dumps({"message": {"content": ""}, "done": True}) + "\n"
        return httpx.Response(200, content=body.encode())

    chunks = [c async for c in _provider(handler).stream([Message.user("x")], model="m")]
    assert "".join(c.delta for c in chunks) == "hi"


# --- tool calling -----------------------------------------------------------


async def test_tool_calls_are_returned_with_synthesised_ids():
    """Ollama does not issue call ids, but the protocol requires them."""
    payload = {
        "model": "llama3.2:latest",
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "adf", "arguments": {"column": "close"}}}
            ],
        },
        "done": True,
        "done_reason": "stop",
    }
    tools = [ToolSpec(name="adf", description="unit root test", input_schema={})]

    result = await _provider(_json_handler(payload)).complete(
        [Message.user("test it")], model="llama3.2:latest", tools=tools
    )

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "adf"
    assert result.tool_calls[0].arguments == {"column": "close"}
    assert result.tool_calls[0].id, "a call id must be synthesised"
    assert result.stop_reason == "tool_use"


async def test_tools_are_sent_in_ollama_function_shape():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=CHAT_RESPONSE)

    tools = [
        ToolSpec(
            name="capm",
            description="fit CAPM",
            input_schema={"type": "object", "properties": {"asset": {"type": "string"}}},
        )
    ]
    await _provider(handler).complete([Message.user("go")], model="m", tools=tools)

    assert captured["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "capm",
                "description": "fit CAPM",
                "parameters": {
                    "type": "object",
                    "properties": {"asset": {"type": "string"}},
                },
            },
        }
    ]


async def test_tool_results_are_sent_back_as_tool_role_messages():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=CHAT_RESPONSE)

    await _provider(handler).complete(
        [
            Message.user("test it"),
            Message.assistant("", tool_calls=[]),
            Message.tool_result("call_1", '{"p_value": 0.21}'),
        ],
        model="m",
    )

    assert captured["messages"][-1]["role"] == "tool"
    assert captured["messages"][-1]["content"] == '{"p_value": 0.21}'


# --- health -----------------------------------------------------------------


async def test_health_reports_reachable_with_a_model_count():
    health = await _provider(_json_handler(TAGS_RESPONSE)).health()
    assert health.reachable is True
    assert health.models_available == 2


async def test_health_never_raises_when_the_daemon_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    health = await _provider(handler).health()
    assert health.reachable is False
    assert "ollama serve" in health.detail


# --- live tests -------------------------------------------------------------


async def _daemon_is_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.get("http://localhost:11434/api/tags")
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_lists_real_models():
    """Proves the tags wire format still matches what the adapter parses."""
    if not await _daemon_is_up():
        pytest.skip("ollama daemon is not running")

    provider = OllamaProvider()
    try:
        models = await provider.list_models()
    finally:
        await provider.aclose()

    assert models, "expected at least one local model"
    assert all(m.id for m in models)


@pytest.mark.live
async def test_live_health_matches_reality():
    if not await _daemon_is_up():
        pytest.skip("ollama daemon is not running")

    provider = OllamaProvider()
    try:
        health = await provider.health()
    finally:
        await provider.aclose()

    assert health.reachable is True
    assert health.models_available > 0
