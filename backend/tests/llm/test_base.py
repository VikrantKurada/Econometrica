"""Contract tests for the provider abstraction.

No network here by design. The abstraction's job is to make five very
different vendor APIs interchangeable, so what matters is the shape of the
contract, and the fake provider is the reference implementation of it.
"""

import pytest

from econometrica.llm.base import LLMProvider
from econometrica.llm.errors import (
    ModelNotFoundError,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailableError,
)
from econometrica.llm.fake import FakeProvider
from econometrica.llm.types import (
    Capabilities,
    Completion,
    Message,
    Role,
    ToolCall,
    ToolSpec,
)


def test_fake_provider_satisfies_the_protocol():
    assert isinstance(FakeProvider(), LLMProvider)


# --- messages ---------------------------------------------------------------


def test_message_helpers_build_each_role():
    assert Message.user("hi").role is Role.USER
    assert Message.system("be terse").role is Role.SYSTEM
    assert Message.assistant("sure").role is Role.ASSISTANT


def test_tool_result_message_carries_its_call_id():
    msg = Message.tool_result("call_1", "0.83")
    assert msg.role is Role.TOOL
    assert msg.tool_call_id == "call_1"
    assert msg.content == "0.83"


def test_assistant_message_can_carry_tool_calls():
    call = ToolCall(id="c1", name="capm", arguments={"asset": "AAPL"})
    msg = Message.assistant("", tool_calls=[call])
    assert msg.tool_calls[0].name == "capm"


def test_tool_result_without_a_call_id_is_rejected():
    """A tool result the provider cannot attribute is a protocol violation."""
    with pytest.raises(ValueError, match="tool_call_id"):
        Message(role=Role.TOOL, content="0.83")


# --- completion -------------------------------------------------------------


async def test_complete_returns_scripted_content_and_usage():
    provider = FakeProvider(responses=["the beta is 1.3"])
    result = await provider.complete([Message.user("what is the beta?")], model="fake-1")

    assert isinstance(result, Completion)
    assert result.content == "the beta is 1.3"
    assert result.model == "fake-1"
    assert result.provider == "fake"
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.latency_ms >= 0


async def test_complete_records_what_it_was_called_with():
    """The fake is also the spy the agent tests will need in Phase 4."""
    provider = FakeProvider(responses=["ok"])
    await provider.complete(
        [Message.system("be terse"), Message.user("hi")], model="fake-1", temperature=0.2
    )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call.model == "fake-1"
    assert call.temperature == 0.2
    assert [m.role for m in call.messages] == [Role.SYSTEM, Role.USER]


async def test_responses_are_consumed_in_order():
    provider = FakeProvider(responses=["first", "second"])
    assert (await provider.complete([Message.user("a")], model="m")).content == "first"
    assert (await provider.complete([Message.user("b")], model="m")).content == "second"


async def test_running_out_of_scripted_responses_raises():
    provider = FakeProvider(responses=["only one"])
    await provider.complete([Message.user("a")], model="m")
    with pytest.raises(AssertionError, match="ran out of scripted responses"):
        await provider.complete([Message.user("b")], model="m")


# --- streaming --------------------------------------------------------------


async def test_stream_yields_deltas_that_reassemble_into_the_content():
    provider = FakeProvider(responses=["hello world"])
    chunks = [c async for c in provider.stream([Message.user("hi")], model="m")]

    assert "".join(c.delta for c in chunks) == "hello world"
    assert chunks[-1].done is True
    assert all(c.done is False for c in chunks[:-1])


async def test_final_stream_chunk_carries_usage_and_stop_reason():
    provider = FakeProvider(responses=["hi"])
    chunks = [c async for c in provider.stream([Message.user("hi")], model="m")]

    final = chunks[-1]
    assert final.usage is not None
    assert final.usage.output_tokens > 0
    assert final.stop_reason == "end_turn"


# --- tool calling -----------------------------------------------------------


async def test_provider_returns_tool_calls_when_scripted():
    call = ToolCall(id="c1", name="adf", arguments={"column": "close"})
    provider = FakeProvider(tool_calls=[[call]])
    tools = [ToolSpec(name="adf", description="unit root test", input_schema={})]

    result = await provider.complete([Message.user("test it")], model="m", tools=tools)

    assert result.tool_calls[0].name == "adf"
    assert result.stop_reason == "tool_use"
    assert provider.calls[0].tools is not None
    assert provider.calls[0].tools[0].name == "adf"


def test_tool_spec_renders_from_a_registry_entry():
    """The econ registry already emits this shape; adapters must accept it."""
    import econometrica.econ.pricing  # noqa: F401 — registration side-effect
    from econometrica.econ.registry import get_registry

    schema = get_registry().to_tool_schemas()[0]
    spec = ToolSpec(**schema)
    assert spec.name == schema["name"]
    assert spec.input_schema == schema["input_schema"]


# --- capabilities and models ------------------------------------------------


async def test_list_models_reports_capabilities():
    provider = FakeProvider()
    models = await provider.list_models()
    assert models[0].id
    assert isinstance(models[0].capabilities, Capabilities)


async def test_unknown_model_raises_model_not_found():
    provider = FakeProvider(responses=["x"], known_models=["fake-1"])
    with pytest.raises(ModelNotFoundError, match="nonexistent"):
        await provider.complete([Message.user("a")], model="nonexistent")


async def test_health_reports_reachability():
    assert (await FakeProvider().health()).reachable is True
    assert (await FakeProvider(reachable=False).health()).reachable is False


# --- error hierarchy --------------------------------------------------------


def test_every_provider_error_is_catchable_as_provider_error():
    """Callers must be able to catch one type and handle any provider failure."""
    for exc in (
        ProviderUnavailableError("ollama", "daemon not running"),
        ProviderAuthError("openai", "bad key"),
        ModelNotFoundError("gemini", "no-such-model"),
    ):
        assert isinstance(exc, ProviderError)
        assert exc.provider


def test_provider_errors_name_the_provider_in_their_message():
    err = ProviderUnavailableError("ollama", "connection refused")
    assert "ollama" in str(err)
    assert "connection refused" in str(err)


async def test_unreachable_provider_raises_unavailable_on_use():
    provider = FakeProvider(reachable=False)
    with pytest.raises(ProviderUnavailableError, match="fake"):
        await provider.complete([Message.user("a")], model="fake-1")


async def test_scripted_failure_propagates():
    provider = FakeProvider(error=ProviderAuthError("fake", "invalid api key"))
    with pytest.raises(ProviderAuthError, match="invalid api key"):
        await provider.complete([Message.user("a")], model="fake-1")
