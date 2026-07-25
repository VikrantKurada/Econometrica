"""Tests for the Anthropic adapter.

Unlike the other adapters, this one drives the official `anthropic` SDK rather
than raw httpx. The tests therefore mock the SDK's *transport* rather than the
SDK itself, so the real serialization, streaming and error-mapping code paths
are exercised.
"""

import json

import httpx
import pytest
from anthropic import DefaultAsyncHttpxClient

from econometrica.llm.errors import (
    ModelNotFoundError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from econometrica.llm.providers.anthropic import (
    NO_SAMPLING_PARAMS,
    AnthropicProvider,
)
from econometrica.llm.types import Message, ToolSpec

MESSAGE_RESPONSE = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-opus-5",
    "content": [{"type": "text", "text": "The beta is 1.3."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 26, "output_tokens": 8},
}


def _provider(handler, **kwargs) -> AnthropicProvider:
    http_client = DefaultAsyncHttpxClient(transport=httpx.MockTransport(handler))
    return AnthropicProvider(api_key="sk-ant-test", http_client=http_client, **kwargs)


def _json_handler(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _capturing_handler(captured: dict, payload=MESSAGE_RESPONSE):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=payload)

    return handler


# --- completion -------------------------------------------------------------


async def test_complete_returns_content_and_usage():
    result = await _provider(_json_handler(MESSAGE_RESPONSE)).complete(
        [Message.user("beta?")], model="claude-opus-5"
    )

    assert result.content == "The beta is 1.3."
    assert result.provider == "anthropic"
    assert result.model == "claude-opus-5"
    assert result.usage.input_tokens == 26
    assert result.usage.output_tokens == 8
    assert result.stop_reason == "end_turn"


async def test_system_messages_become_the_system_parameter():
    """Anthropic takes the system prompt as its own field, not a message role."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.system("be terse"), Message.user("beta?")], model="claude-opus-5"
    )

    assert captured["body"]["messages"] == [{"role": "user", "content": "beta?"}]
    assert captured["body"]["system"][0]["text"] == "be terse"


async def test_multiple_system_messages_are_joined_in_order():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.system("first"), Message.system("second"), Message.user("hi")],
        model="claude-opus-5",
    )

    texts = [block["text"] for block in captured["body"]["system"]]
    assert texts == ["first", "second"]


async def test_max_tokens_is_always_sent():
    """The Anthropic API requires max_tokens; the protocol makes it optional."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="claude-opus-5"
    )

    assert captured["body"]["max_tokens"] > 0


async def test_explicit_max_tokens_is_respected():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="claude-opus-5", max_tokens=512
    )

    assert captured["body"]["max_tokens"] == 512


# --- sampling parameters ----------------------------------------------------


@pytest.mark.parametrize("model", sorted(NO_SAMPLING_PARAMS))
async def test_temperature_is_omitted_on_models_that_reject_it(model):
    """These models return 400 if temperature is sent at all.

    The protocol exposes temperature uniformly across five providers, so the
    adapter has to drop it rather than let the caller discover the limitation.
    """
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model=model, temperature=0.7
    )

    assert "temperature" not in captured["body"]


async def test_temperature_is_sent_on_models_that_accept_it():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="claude-haiku-4-5", temperature=0.7
    )

    assert captured["body"]["temperature"] == 0.7


# --- prompt caching ---------------------------------------------------------


async def test_system_prompt_carries_a_cache_breakpoint():
    """Agent system prompts are large and stable — the whole point of caching."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.system("a long stable agent prompt"), Message.user("hi")],
        model="claude-opus-5",
    )

    assert captured["body"]["system"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_only_the_last_system_block_carries_the_breakpoint():
    """Caching is a prefix match; one breakpoint covers everything before it."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.system("first"), Message.system("second"), Message.user("hi")],
        model="claude-opus-5",
    )

    blocks = captured["body"]["system"]
    assert "cache_control" not in blocks[0]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


async def test_caching_can_be_disabled():
    captured: dict = {}

    await _provider(_capturing_handler(captured), cache_system_prompt=False).complete(
        [Message.system("prompt"), Message.user("hi")], model="claude-opus-5"
    )

    assert "cache_control" not in captured["body"]["system"][0]


async def test_cache_usage_is_reported():
    payload = dict(MESSAGE_RESPONSE)
    payload["usage"] = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 2048,
    }

    result = await _provider(_json_handler(payload)).complete(
        [Message.user("hi")], model="claude-opus-5"
    )

    assert result.usage.cache_read_tokens == 2048


async def test_no_system_block_means_no_system_field():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="claude-opus-5"
    )

    assert "system" not in captured["body"]


# --- tool use ---------------------------------------------------------------


async def test_tool_calls_are_parsed_with_native_ids():
    """Anthropic returns tool input already parsed — no JSON string to decode."""
    payload = dict(MESSAGE_RESPONSE)
    payload["content"] = [
        {"type": "text", "text": "Let me check."},
        {
            "type": "tool_use",
            "id": "toolu_abc",
            "name": "adf",
            "input": {"column": "close"},
        },
    ]
    payload["stop_reason"] = "tool_use"

    result = await _provider(_json_handler(payload)).complete(
        [Message.user("test it")], model="claude-opus-5", tools=[ToolSpec(name="adf")]
    )

    assert result.tool_calls[0].id == "toolu_abc"
    assert result.tool_calls[0].name == "adf"
    assert result.tool_calls[0].arguments == {"column": "close"}
    assert result.stop_reason == "tool_use"
    assert result.content == "Let me check.", "text alongside a tool call is kept"


async def test_tools_are_sent_in_anthropic_shape():
    captured: dict = {}
    tools = [
        ToolSpec(
            name="capm",
            description="fit CAPM",
            input_schema={"type": "object", "properties": {"asset": {"type": "string"}}},
        )
    ]

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("go")], model="claude-opus-5", tools=tools
    )

    assert captured["body"]["tools"][0] == {
        "name": "capm",
        "description": "fit CAPM",
        "input_schema": {"type": "object", "properties": {"asset": {"type": "string"}}},
    }


async def test_tool_results_become_user_content_blocks():
    """Anthropic carries tool results as blocks in a user turn, not a tool role."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [
            Message.user("test it"),
            Message.tool_result("toolu_abc", '{"p_value": 0.21}'),
        ],
        model="claude-opus-5",
    )

    last = captured["body"]["messages"][-1]
    assert last["role"] == "user"
    assert last["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_abc",
        "content": '{"p_value": 0.21}',
    }


async def test_consecutive_tool_results_are_merged_into_one_turn():
    """Splitting parallel tool results across turns degrades parallel tool use."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [
            Message.user("test both"),
            Message.tool_result("toolu_1", "first"),
            Message.tool_result("toolu_2", "second"),
        ],
        model="claude-opus-5",
    )

    last = captured["body"]["messages"][-1]
    assert len(last["content"]) == 2
    assert [b["tool_use_id"] for b in last["content"]] == ["toolu_1", "toolu_2"]


async def test_assistant_tool_calls_round_trip():
    captured: dict = {}
    from econometrica.llm.types import ToolCall

    await _provider(_capturing_handler(captured)).complete(
        [
            Message.user("go"),
            Message.assistant(
                "checking", tool_calls=[ToolCall(id="toolu_1", name="adf", arguments={"lags": 4})]
            ),
            Message.tool_result("toolu_1", "0.21"),
        ],
        model="claude-opus-5",
    )

    assistant = captured["body"]["messages"][1]
    assert assistant["role"] == "assistant"
    assert assistant["content"][-1] == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "adf",
        "input": {"lags": 4},
    }


# --- refusals ---------------------------------------------------------------


async def test_refusal_is_surfaced_rather_than_read_as_content():
    """A refusal is HTTP 200 with empty content — indexing content[0] would crash."""
    payload = dict(MESSAGE_RESPONSE)
    payload["content"] = []
    payload["stop_reason"] = "refusal"

    result = await _provider(_json_handler(payload)).complete(
        [Message.user("something disallowed")], model="claude-opus-5"
    )

    assert result.stop_reason == "refusal"
    assert result.content == ""
    assert result.refused is True


async def test_a_normal_response_is_not_marked_refused():
    result = await _provider(_json_handler(MESSAGE_RESPONSE)).complete(
        [Message.user("hi")], model="claude-opus-5"
    )
    assert result.refused is False


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (529, ProviderUnavailableError),
        (400, ProviderResponseError),
    ],
)
async def test_sdk_exceptions_map_onto_the_provider_hierarchy(status, expected):
    payload = {"type": "error", "error": {"type": "x", "message": "boom"}}
    provider = _provider(_json_handler(payload, status=status), max_retries=0)

    with pytest.raises(expected):
        await provider.complete([Message.user("hi")], model="claude-opus-5")


async def test_unknown_model_raises_model_not_found():
    payload = {
        "type": "error",
        "error": {"type": "not_found_error", "message": "model: ghost"},
    }
    provider = _provider(_json_handler(payload, status=404), max_retries=0)

    with pytest.raises(ModelNotFoundError, match="ghost"):
        await provider.complete([Message.user("hi")], model="ghost")


async def test_connection_failure_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    provider = _provider(handler, max_retries=0)
    with pytest.raises(ProviderUnavailableError):
        await provider.complete([Message.user("hi")], model="claude-opus-5")


async def test_the_api_key_never_appears_in_an_error_message():
    payload = {
        "type": "error",
        "error": {"type": "authentication_error", "message": "bad key sk-ant-test"},
    }
    provider = _provider(_json_handler(payload, status=401), max_retries=0)

    with pytest.raises(ProviderAuthError) as exc:
        await provider.complete([Message.user("hi")], model="claude-opus-5")

    assert "sk-ant-test" not in str(exc.value)


async def test_a_missing_api_key_fails_before_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have issued a request")

    provider = _provider(handler)
    provider.api_key = ""

    with pytest.raises(ProviderAuthError, match="no api key"):
        await provider.complete([Message.user("hi")], model="claude-opus-5")


# --- streaming --------------------------------------------------------------


def _sse_handler(events):
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(
            f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return handler


TEXT_STREAM = [
    {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 26, "output_tokens": 0},
        },
    },
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "The beta "},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": "is 1.3."},
    },
    {"type": "content_block_stop", "index": 0},
    {
        "type": "message_delta",
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": 8},
    },
    {"type": "message_stop"},
]


async def test_stream_reassembles_content():
    chunks = [
        c
        async for c in _provider(_sse_handler(TEXT_STREAM)).stream(
            [Message.user("beta?")], model="claude-opus-5"
        )
    ]

    assert "".join(c.delta for c in chunks) == "The beta is 1.3."
    assert chunks[-1].done is True
    assert chunks[-1].stop_reason == "end_turn"


async def test_final_stream_chunk_carries_usage():
    chunks = [
        c
        async for c in _provider(_sse_handler(TEXT_STREAM)).stream(
            [Message.user("beta?")], model="claude-opus-5"
        )
    ]

    final = chunks[-1]
    assert final.usage is not None
    assert final.usage.input_tokens == 26
    assert final.usage.output_tokens == 8


async def test_streamed_tool_calls_are_assembled():
    """Anthropic streams tool input as partial JSON across deltas."""
    events = [
        TEXT_STREAM[0],
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "adf", "input": {}},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": '{"col'},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": 'umn": "close"}'},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 12},
        },
        {"type": "message_stop"},
    ]

    chunks = [
        c
        async for c in _provider(_sse_handler(events)).stream(
            [Message.user("test it")], model="claude-opus-5"
        )
    ]

    final = chunks[-1]
    assert final.done is True
    assert final.tool_calls[0].id == "toolu_1"
    assert final.tool_calls[0].name == "adf"
    assert final.tool_calls[0].arguments == {"column": "close"}
    assert final.stop_reason == "tool_use"


async def test_stream_marks_a_refusal():
    events = [
        TEXT_STREAM[0],
        {
            "type": "message_delta",
            "delta": {"stop_reason": "refusal", "stop_sequence": None},
            "usage": {"output_tokens": 0},
        },
        {"type": "message_stop"},
    ]

    chunks = [
        c
        async for c in _provider(_sse_handler(events)).stream(
            [Message.user("x")], model="claude-opus-5"
        )
    ]

    assert chunks[-1].stop_reason == "refusal"


# --- models and health ------------------------------------------------------


MODELS_RESPONSE = {
    "data": [
        {
            "type": "model",
            "id": "claude-opus-5",
            "display_name": "Claude Opus 5",
            "created_at": "2026-01-01T00:00:00Z",
            "max_input_tokens": 1000000,
            "max_tokens": 128000,
            "capabilities": {
                "image_input": {"supported": True},
                "structured_outputs": {"supported": True},
            },
        }
    ],
    "has_more": False,
    "first_id": "claude-opus-5",
    "last_id": "claude-opus-5",
}


async def test_list_models_reports_real_context_windows():
    """The Models API is authoritative — no hard-coded capability table."""
    models = await _provider(_json_handler(MODELS_RESPONSE)).list_models()

    assert models[0].id == "claude-opus-5"
    assert models[0].name == "Claude Opus 5"
    assert models[0].capabilities.context_window == 1000000
    assert models[0].capabilities.vision is True
    assert models[0].capabilities.tool_calling is True


async def test_health_reports_reachable_with_a_model_count():
    health = await _provider(_json_handler(MODELS_RESPONSE)).health()
    assert health.reachable is True
    assert health.models_available == 1


async def test_health_of_an_unconfigured_provider_is_not_reachable():
    health = await AnthropicProvider(api_key="").health()
    assert health.reachable is False
    assert "api key" in health.detail.lower()


async def test_health_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    health = await _provider(handler, max_retries=0).health()
    assert health.reachable is False
    assert health.detail
