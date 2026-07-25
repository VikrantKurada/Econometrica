"""Tests for the Gemini adapter.

Gemini diverges from the OpenAI shape more than any other provider here:
`contents`/`parts` instead of `messages`/`content`, the role `model` instead of
`assistant`, the system prompt as its own `systemInstruction` field, and
function results matched back to their call **by name** rather than by id.
That last one is the interesting case — the protocol hands the adapter a
`tool_call_id`, so it has to resolve the name from the conversation itself.
"""

import json

import httpx
import pytest

from econometrica.llm.errors import (
    ModelNotFoundError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from econometrica.llm.providers.gemini import GeminiProvider
from econometrica.llm.types import Message, ToolCall, ToolSpec

GENERATE_RESPONSE = {
    "candidates": [
        {
            "content": {"parts": [{"text": "The beta is 1.3."}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 26,
        "candidatesTokenCount": 8,
        "totalTokenCount": 34,
    },
    "modelVersion": "gemini-2.5-pro",
}


def _provider(handler, **kwargs) -> GeminiProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    return GeminiProvider(api_key="AIza-test-key", client=client, **kwargs)


def _json_handler(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _capturing_handler(captured: dict, payload=GENERATE_RESPONSE):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        return httpx.Response(200, json=payload)

    return handler


# --- completion -------------------------------------------------------------


async def test_complete_returns_content_and_usage():
    result = await _provider(_json_handler(GENERATE_RESPONSE)).complete(
        [Message.user("beta?")], model="gemini-2.5-pro"
    )

    assert result.content == "The beta is 1.3."
    assert result.provider == "gemini"
    assert result.usage.input_tokens == 26
    assert result.usage.output_tokens == 8
    assert result.stop_reason == "stop"


async def test_multiple_text_parts_are_joined():
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": "The beta "}, {"text": "is 1.3."}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }
    result = await _provider(_json_handler(payload)).complete(
        [Message.user("x")], model="m"
    )
    assert result.content == "The beta is 1.3."


async def test_api_key_travels_in_a_header_not_the_url():
    """A key in the query string leaks into proxy and server access logs."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="gemini-2.5-pro"
    )

    assert captured["headers"]["x-goog-api-key"] == "AIza-test-key"
    assert "AIza-test-key" not in captured["url"]
    assert "key=" not in captured["url"]


async def test_assistant_role_is_renamed_to_model():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi"), Message.assistant("hello"), Message.user("again")],
        model="m",
    )

    assert [c["role"] for c in captured["body"]["contents"]] == ["user", "model", "user"]


async def test_content_is_wrapped_in_parts():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("beta?")], model="m"
    )

    assert captured["body"]["contents"][0]["parts"] == [{"text": "beta?"}]


async def test_system_messages_become_system_instruction():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.system("be terse"), Message.user("hi")], model="m"
    )

    assert captured["body"]["systemInstruction"]["parts"] == [{"text": "be terse"}]
    assert [c["role"] for c in captured["body"]["contents"]] == ["user"]


async def test_multiple_system_messages_are_joined():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.system("first"), Message.system("second"), Message.user("hi")], model="m"
    )

    parts = captured["body"]["systemInstruction"]["parts"]
    assert [p["text"] for p in parts] == ["first", "second"]


async def test_no_system_message_means_no_system_instruction():
    captured: dict = {}
    await _provider(_capturing_handler(captured)).complete([Message.user("hi")], model="m")
    assert "systemInstruction" not in captured["body"]


async def test_generation_config_carries_temperature_and_max_tokens():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="m", temperature=0.4, max_tokens=256
    )

    config = captured["body"]["generationConfig"]
    assert config["temperature"] == 0.4
    assert config["maxOutputTokens"] == 256


async def test_json_mode_sets_the_response_mime_type():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("hi")], model="m", json_mode=True
    )

    assert captured["body"]["generationConfig"]["responseMimeType"] == "application/json"


# --- finish reasons and refusals -------------------------------------------


@pytest.mark.parametrize(
    ("finish", "expected"),
    [
        ("STOP", "stop"),
        ("MAX_TOKENS", "max_tokens"),
        ("SAFETY", "refusal"),
        ("PROHIBITED_CONTENT", "refusal"),
        ("RECITATION", "recitation"),
    ],
)
async def test_finish_reasons_normalise_across_providers(finish, expected):
    payload = {
        "candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 0},
    }
    result = await _provider(_json_handler(payload)).complete([Message.user("x")], model="m")
    assert result.stop_reason == expected


async def test_a_safety_block_is_reported_as_a_refusal():
    """Gemini blocks with an empty candidate — reading parts[0] would crash."""
    payload = {
        "candidates": [{"finishReason": "SAFETY", "index": 0}],
        "usageMetadata": {"promptTokenCount": 5},
    }
    result = await _provider(_json_handler(payload)).complete([Message.user("x")], model="m")

    assert result.refused is True
    assert result.content == ""


async def test_a_prompt_level_block_is_also_a_refusal():
    """When the *prompt* is blocked there is no candidates array at all."""
    payload = {
        "promptFeedback": {"blockReason": "SAFETY"},
        "usageMetadata": {"promptTokenCount": 5},
    }
    result = await _provider(_json_handler(payload)).complete([Message.user("x")], model="m")

    assert result.refused is True
    assert result.content == ""


# --- tool use ---------------------------------------------------------------


async def test_tools_are_sent_as_function_declarations():
    captured: dict = {}
    tools = [
        ToolSpec(
            name="capm",
            description="fit CAPM",
            input_schema={"type": "object", "properties": {"asset": {"type": "string"}}},
        )
    ]

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("go")], model="m", tools=tools
    )

    assert captured["body"]["tools"] == [
        {
            "functionDeclarations": [
                {
                    "name": "capm",
                    "description": "fit CAPM",
                    "parameters": {
                        "type": "object",
                        "properties": {"asset": {"type": "string"}},
                    },
                }
            ]
        }
    ]


async def test_an_empty_schema_is_omitted_rather_than_sent_empty():
    """Gemini rejects a functionDeclaration whose parameters have no properties."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [Message.user("go")], model="m", tools=[ToolSpec(name="ping")]
    )

    declaration = captured["body"]["tools"][0]["functionDeclarations"][0]
    assert "parameters" not in declaration


async def test_function_calls_are_returned_with_synthesised_ids():
    """Gemini issues no call ids, but the protocol requires them."""
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"functionCall": {"name": "adf", "args": {"column": "close"}}}
                    ],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
    }

    result = await _provider(_json_handler(payload)).complete(
        [Message.user("test it")], model="m", tools=[ToolSpec(name="adf")]
    )

    assert result.tool_calls[0].name == "adf"
    assert result.tool_calls[0].arguments == {"column": "close"}
    assert result.tool_calls[0].id, "a call id must be synthesised"
    assert result.stop_reason == "tool_use"


async def test_tool_results_are_matched_back_to_their_call_by_name():
    """The crux of this adapter.

    Gemini keys a functionResponse by function *name*; the protocol carries a
    `tool_call_id`. The adapter therefore has to look back through the
    conversation to find which call that id belonged to.
    """
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [
            Message.user("test it"),
            Message.assistant(
                "", tool_calls=[ToolCall(id="call_xyz", name="adf", arguments={})]
            ),
            Message.tool_result("call_xyz", '{"p_value": 0.21}'),
        ],
        model="m",
    )

    last = captured["body"]["contents"][-1]
    assert last["role"] == "user"
    assert last["parts"][0]["functionResponse"]["name"] == "adf"
    assert last["parts"][0]["functionResponse"]["response"] == {"p_value": 0.21}


async def test_a_non_json_tool_result_is_wrapped_rather_than_dropped():
    """functionResponse.response must be an object; tools may return plain text."""
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [
            Message.user("go"),
            Message.assistant("", tool_calls=[ToolCall(id="c1", name="ping", arguments={})]),
            Message.tool_result("c1", "pong"),
        ],
        model="m",
    )

    response = captured["body"]["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert response == {"result": "pong"}


async def test_an_unmatched_tool_result_raises_rather_than_sending_a_bad_name():
    """Silently guessing the function name would produce a confusing 400."""
    provider = _provider(_json_handler(GENERATE_RESPONSE))

    with pytest.raises(ProviderResponseError, match="orphan"):
        await provider.complete(
            [Message.user("go"), Message.tool_result("nope", "{}")], model="m"
        )


async def test_assistant_tool_calls_round_trip_as_function_call_parts():
    captured: dict = {}

    await _provider(_capturing_handler(captured)).complete(
        [
            Message.user("go"),
            Message.assistant(
                "checking", tool_calls=[ToolCall(id="c1", name="adf", arguments={"lags": 4})]
            ),
            Message.tool_result("c1", "{}"),
        ],
        model="m",
    )

    parts = captured["body"]["contents"][1]["parts"]
    assert parts[0] == {"text": "checking"}
    assert parts[1] == {"functionCall": {"name": "adf", "args": {"lags": 4}}}


# --- streaming --------------------------------------------------------------


def _sse_handler(events):
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(f"data: {json.dumps(e)}\r\n\r\n" for e in events)
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    return handler


async def test_stream_reassembles_content():
    events = [
        {"candidates": [{"content": {"parts": [{"text": "The beta "}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "is 1.3."}]}}]},
        {
            "candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 26, "candidatesTokenCount": 8},
        },
    ]

    chunks = [
        c async for c in _provider(_sse_handler(events)).stream([Message.user("x")], model="m")
    ]

    assert "".join(c.delta for c in chunks) == "The beta is 1.3."
    assert chunks[-1].done is True
    assert chunks[-1].stop_reason == "stop"
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.output_tokens == 8


async def test_stream_requests_sse_alt_mode():
    """Without alt=sse Gemini streams a JSON array, not server-sent events."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(
            200, content=b"", headers={"content-type": "text/event-stream"}
        )

    async for _ in _provider(handler).stream([Message.user("x")], model="m"):
        pass

    assert "alt=sse" in captured["url"]
    assert "streamGenerateContent" in captured["url"]


async def test_streamed_function_calls_are_collected():
    events = [
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "adf", "args": {"lags": 4}}}]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2},
        }
    ]

    chunks = [
        c async for c in _provider(_sse_handler(events)).stream([Message.user("x")], model="m")
    ]

    final = chunks[-1]
    assert final.tool_calls[0].name == "adf"
    assert final.tool_calls[0].arguments == {"lags": 4}
    assert final.stop_reason == "tool_use"


async def test_malformed_stream_events_are_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "data: not-json\r\n\r\n"
            'data: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}\r\n\r\n'
        )
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}
        )

    chunks = [c async for c in _provider(handler).stream([Message.user("x")], model="m")]
    assert "".join(c.delta for c in chunks) == "ok"


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "gstatus", "expected"),
    [
        (400, "INVALID_ARGUMENT", ProviderResponseError),
        (400, "FAILED_PRECONDITION", ProviderResponseError),
        (401, "UNAUTHENTICATED", ProviderAuthError),
        (403, "PERMISSION_DENIED", ProviderAuthError),
        (429, "RESOURCE_EXHAUSTED", ProviderRateLimitError),
        (500, "INTERNAL", ProviderUnavailableError),
        (503, "UNAVAILABLE", ProviderUnavailableError),
    ],
)
async def test_http_status_maps_to_the_right_error(status, gstatus, expected):
    payload = {"error": {"code": status, "message": "boom", "status": gstatus}}
    with pytest.raises(expected):
        await _provider(_json_handler(payload, status=status)).complete(
            [Message.user("x")], model="m"
        )


async def test_an_invalid_key_reported_as_400_is_still_an_auth_error():
    """Gemini returns 400 INVALID_ARGUMENT for a bad key, not 401."""
    payload = {
        "error": {
            "code": 400,
            "message": "API key not valid. Please pass a valid API key.",
            "status": "INVALID_ARGUMENT",
        }
    }
    with pytest.raises(ProviderAuthError):
        await _provider(_json_handler(payload, status=400)).complete(
            [Message.user("x")], model="m"
        )


async def test_unknown_model_raises_model_not_found():
    payload = {
        "error": {"code": 404, "message": "models/ghost is not found", "status": "NOT_FOUND"}
    }
    with pytest.raises(ModelNotFoundError, match="ghost"):
        await _provider(_json_handler(payload, status=404)).complete(
            [Message.user("x")], model="ghost"
        )


@pytest.mark.parametrize("status", [400, 403, 429, 500])
async def test_the_api_key_never_appears_in_an_error_message(status):
    """Redaction must hold for every error class, not just auth failures.

    Errors reach logs and the UI, so any path that echoes the request back is a
    leak. Asserting on the base class covers all of them.
    """
    payload = {
        "error": {"code": status, "message": "rejected key AIza-test-key", "status": "X"}
    }
    with pytest.raises(ProviderError) as exc:
        await _provider(_json_handler(payload, status=status)).complete(
            [Message.user("x")], model="m"
        )
    assert "AIza-test-key" not in str(exc.value)


async def test_connection_failure_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(ProviderUnavailableError):
        await _provider(handler).complete([Message.user("x")], model="m")


async def test_a_missing_api_key_fails_before_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have issued a request")

    provider = _provider(handler)
    provider.api_key = ""

    with pytest.raises(ProviderAuthError, match="no api key"):
        await provider.complete([Message.user("x")], model="m")


# --- models and health ------------------------------------------------------


MODELS_RESPONSE = {
    "models": [
        {
            "name": "models/gemini-2.5-pro",
            "displayName": "Gemini 2.5 Pro",
            "inputTokenLimit": 1048576,
            "outputTokenLimit": 65536,
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        },
        {
            "name": "models/text-embedding-004",
            "displayName": "Text Embedding 004",
            "inputTokenLimit": 2048,
            "supportedGenerationMethods": ["embedContent"],
        },
    ]
}


async def test_list_models_strips_the_models_prefix():
    models = await _provider(_json_handler(MODELS_RESPONSE)).list_models()
    assert models[0].id == "gemini-2.5-pro"
    assert models[0].name == "Gemini 2.5 Pro"
    assert models[0].capabilities.context_window == 1048576


async def test_embedding_only_models_are_excluded():
    """A model that cannot generateContent is not a chat model."""
    models = await _provider(_json_handler(MODELS_RESPONSE)).list_models()
    assert [m.id for m in models] == ["gemini-2.5-pro"]


async def test_health_reports_reachable_with_a_model_count():
    health = await _provider(_json_handler(MODELS_RESPONSE)).health()
    assert health.reachable is True
    assert health.models_available == 1


async def test_health_of_an_unconfigured_provider_is_not_reachable():
    health = await GeminiProvider(api_key="").health()
    assert health.reachable is False
    assert "api key" in health.detail.lower()


async def test_health_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    health = await _provider(handler).health()
    assert health.reachable is False
    assert health.detail
