"""Tests for the OpenAI-compatible transport and its two adapters.

OpenAI and NVIDIA NIM speak the same protocol at different hosts, so one
transport serves both. The tests assert that shared behaviour once, then check
the two adapters differ in exactly the ways they should: base URL, credential
source and name.
"""

import json

import httpx
import pytest

from econometrica.llm.errors import (
    ModelNotFoundError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from econometrica.llm.providers.nvidia import NvidiaProvider
from econometrica.llm.providers.openai import OpenAIProvider
from econometrica.llm.types import Message, ToolSpec

CHAT_RESPONSE = {
    "id": "chatcmpl-1",
    "model": "gpt-4o-mini",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "The beta is 1.3."},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 26, "completion_tokens": 8, "total_tokens": 34},
}

MODELS_RESPONSE = {
    "data": [
        {"id": "gpt-4o-mini", "object": "model"},
        {"id": "gpt-4o", "object": "model"},
    ]
}


def _openai(handler, **kwargs) -> OpenAIProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.openai.com/v1"
    )
    return OpenAIProvider(api_key="sk-test", client=client, **kwargs)


def _json_handler(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


# --- shared completion behaviour -------------------------------------------


async def test_complete_returns_content_and_usage():
    result = await _openai(_json_handler(CHAT_RESPONSE)).complete(
        [Message.user("beta?")], model="gpt-4o-mini"
    )

    assert result.content == "The beta is 1.3."
    assert result.provider == "openai"
    assert result.usage.input_tokens == 26
    assert result.usage.output_tokens == 8
    assert result.stop_reason == "stop"


async def test_request_carries_bearer_auth_and_the_conversation():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=CHAT_RESPONSE)

    await _openai(handler).complete(
        [Message.system("be terse"), Message.user("beta?")],
        model="gpt-4o-mini",
        temperature=0.4,
        max_tokens=256,
    )

    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["model"] == "gpt-4o-mini"
    assert captured["body"]["temperature"] == 0.4
    assert captured["body"]["max_tokens"] == 256
    assert captured["body"]["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "beta?"},
    ]


async def test_json_mode_sets_the_response_format():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=CHAT_RESPONSE)

    await _openai(handler).complete([Message.user("x")], model="m", json_mode=True)
    assert captured["response_format"] == {"type": "json_object"}


async def test_list_models():
    models = await _openai(_json_handler(MODELS_RESPONSE)).list_models()
    assert [m.id for m in models] == ["gpt-4o-mini", "gpt-4o"]


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
    ],
)
async def test_http_status_maps_to_the_right_error(status, expected):
    """Callers retry rate limits and surface auth failures — the type decides."""
    provider = _openai(_json_handler({"error": {"message": "boom"}}, status=status))
    with pytest.raises(expected):
        await provider.complete([Message.user("x")], model="m")


async def test_unknown_model_raises_model_not_found():
    payload = {"error": {"message": "The model `ghost` does not exist", "code": "model_not_found"}}
    with pytest.raises(ModelNotFoundError, match="ghost"):
        await _openai(_json_handler(payload, status=404)).complete(
            [Message.user("x")], model="ghost"
        )


async def test_rate_limit_carries_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {}}, headers={"retry-after": "30"})

    with pytest.raises(ProviderRateLimitError) as exc:
        await _openai(handler).complete([Message.user("x")], model="m")

    assert exc.value.retry_after == 30.0


async def test_the_api_key_never_appears_in_an_error_message():
    """Errors reach logs and the UI; a leaked key there is a real incident."""
    payload = {"error": {"message": "Incorrect API key provided: sk-test"}}
    provider = _openai(_json_handler(payload, status=401))

    with pytest.raises(ProviderAuthError) as exc:
        await provider.complete([Message.user("x")], model="m")

    assert "sk-test" not in str(exc.value)


async def test_a_missing_api_key_fails_before_any_request():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have issued a request")

    provider = _openai(handler)
    provider.api_key = ""

    with pytest.raises(ProviderAuthError, match="no api key"):
        await provider.complete([Message.user("x")], model="m")


# --- streaming --------------------------------------------------------------


def _sse_handler(events):
    def handler(request: httpx.Request) -> httpx.Response:
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, content=body.encode())

    return handler


async def test_stream_reassembles_content():
    events = [
        {"choices": [{"delta": {"content": "The "}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "beta "}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "is 1.3."}, "finish_reason": "stop"}]},
    ]
    chunks = [
        c async for c in _openai(_sse_handler(events)).stream([Message.user("x")], model="m")
    ]

    assert "".join(c.delta for c in chunks) == "The beta is 1.3."
    assert chunks[-1].done is True
    assert chunks[-1].stop_reason == "stop"


async def test_stream_reports_usage_when_the_provider_sends_it():
    events = [
        {"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    ]
    chunks = [
        c async for c in _openai(_sse_handler(events)).stream([Message.user("x")], model="m")
    ]

    assert chunks[-1].usage is not None
    assert chunks[-1].usage.input_tokens == 5


async def test_stream_requests_usage_in_the_final_chunk():
    """Without this option OpenAI streams report no token counts at all."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    async for _ in _openai(handler).stream([Message.user("x")], model="m"):
        pass

    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}


async def test_malformed_stream_events_are_skipped_not_fatal():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            "data: not-json\n\n"
            'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=body.encode())

    chunks = [c async for c in _openai(handler).stream([Message.user("x")], model="m")]
    assert "".join(c.delta for c in chunks) == "ok"


# --- tool calling -----------------------------------------------------------


async def test_tool_calls_are_parsed_with_their_ids():
    payload = {
        "model": "gpt-4o-mini",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "adf",
                                "arguments": '{"column": "close"}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    result = await _openai(_json_handler(payload)).complete(
        [Message.user("x")], model="m", tools=[ToolSpec(name="adf")]
    )

    assert result.tool_calls[0].id == "call_abc"
    assert result.tool_calls[0].name == "adf"
    assert result.tool_calls[0].arguments == {"column": "close"}
    assert result.stop_reason == "tool_use"
    assert result.content == "", "a null content must normalise to empty string"


async def test_tool_arguments_that_are_not_valid_json_are_preserved_not_dropped():
    """Models do emit malformed arguments; losing them hides the failure."""
    payload = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "adf", "arguments": "{oops"}}
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }
    result = await _openai(_json_handler(payload)).complete([Message.user("x")], model="m")
    assert result.tool_calls[0].arguments == {"_raw": "{oops"}


async def test_tool_results_round_trip_with_their_call_id():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=CHAT_RESPONSE)

    await _openai(handler).complete(
        [Message.user("x"), Message.tool_result("call_abc", "0.21")], model="m"
    )

    assert captured["messages"][-1] == {
        "role": "tool",
        "content": "0.21",
        "tool_call_id": "call_abc",
    }


async def test_streamed_tool_calls_are_accumulated_across_deltas():
    """OpenAI splits tool arguments across chunks; naive parsing loses them."""
    events = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "adf", "arguments": '{"col'}}
        ]}, "finish_reason": None}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'umn": "close"}'}}
        ]}, "finish_reason": "tool_calls"}]},
    ]
    chunks = [
        c async for c in _openai(_sse_handler(events)).stream([Message.user("x")], model="m")
    ]

    final = chunks[-1]
    assert final.done is True
    assert len(final.tool_calls) == 1
    assert final.tool_calls[0].name == "adf"
    assert final.tool_calls[0].arguments == {"column": "close"}


# --- the two adapters differ only where they should ------------------------


def test_nvidia_targets_its_own_host_and_name():
    provider = NvidiaProvider(api_key="nvapi-test")
    assert provider.name == "nvidia"
    assert "integrate.api.nvidia.com" in provider.base_url


def test_openai_targets_its_own_host_and_name():
    provider = OpenAIProvider(api_key="sk-test")
    assert provider.name == "openai"
    assert "api.openai.com" in provider.base_url


async def test_nvidia_reuses_the_same_transport_behaviour():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_json_handler(CHAT_RESPONSE)),
        base_url="https://integrate.api.nvidia.com/v1",
    )
    result = await NvidiaProvider(api_key="nvapi-test", client=client).complete(
        [Message.user("beta?")], model="meta/llama-3.1-70b-instruct"
    )

    assert result.content == "The beta is 1.3."
    assert result.provider == "nvidia"


async def test_health_reports_unreachable_without_raising():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    health = await _openai(handler).health()
    assert health.reachable is False
    assert health.detail


async def test_health_of_an_unconfigured_provider_is_not_reachable():
    provider = OpenAIProvider(api_key="")
    health = await provider.health()
    assert health.reachable is False
    assert "api key" in health.detail.lower()
