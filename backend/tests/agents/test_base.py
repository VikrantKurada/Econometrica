"""The ask-parse-retry loop every agent inherits.

A model that returns unusable output is the normal case, not the exceptional
one, so the retry lives in one place and each agent gets it by existing.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel, Field

from econometrica.agents.base import Agent, AgentAttemptsExhaustedError, AgentRefusedError
from econometrica.llm.fake import FakeProvider
from econometrica.llm.types import Completion, Message, ModelInfo, ProviderHealth, StreamChunk


class Answer(BaseModel):
    verdict: str
    score: int = Field(ge=0, le=10)


class ScoringAgent(Agent[Answer]):
    role = "scorer"

    def output_model(self) -> type[Answer]:
        return Answer


def conversation() -> list[Message]:
    return [Message.system("You score things."), Message.user("Score this.")]


async def test_a_valid_reply_is_parsed_into_the_output_model():
    provider = FakeProvider(responses=['{"verdict": "sound", "score": 8}'])

    result = await ScoringAgent(provider, "fake-1").ask(conversation())

    assert result.output == Answer(verdict="sound", score=8)
    assert result.attempts == 1


async def test_the_request_is_deterministic_and_asks_for_json():
    """Two sources of avoidable malformed output, closed at the request."""
    provider = FakeProvider(responses=['{"verdict": "ok", "score": 1}'])

    await ScoringAgent(provider, "fake-1").ask(conversation())

    assert provider.calls[0].temperature == 0.0
    assert provider.calls[0].json_mode is True
    assert provider.calls[0].model == "fake-1"


async def test_a_malformed_reply_is_retried_and_can_succeed():
    provider = FakeProvider(
        responses=["I'd rather describe it in words.", '{"verdict": "ok", "score": 3}']
    )

    result = await ScoringAgent(provider, "fake-1").ask(conversation())

    assert result.output.score == 3
    assert result.attempts == 2


async def test_a_reply_that_parses_but_fails_the_schema_is_retried():
    """Valid JSON is not the same as a valid answer."""
    provider = FakeProvider(
        responses=['{"verdict": "ok", "score": 99}', '{"verdict": "ok", "score": 9}']
    )

    result = await ScoringAgent(provider, "fake-1").ask(conversation())

    assert result.output.score == 9


async def test_the_retry_shows_the_model_its_own_reply_and_the_problem():
    """A retry that only says "invalid" gets the same invalid answer back."""
    provider = FakeProvider(responses=["not json at all", '{"verdict": "ok", "score": 1}'])

    await ScoringAgent(provider, "fake-1").ask(conversation())

    retry_messages = provider.calls[1].messages
    assert any(m.content == "not json at all" for m in retry_messages)
    assert any("no JSON object" in m.content for m in retry_messages)


async def test_the_retry_carries_the_schema_error_when_validation_failed():
    provider = FakeProvider(
        responses=['{"verdict": "ok", "score": 99}', '{"verdict": "ok", "score": 9}']
    )

    await ScoringAgent(provider, "fake-1").ask(conversation())

    assert any("score" in m.content for m in provider.calls[1].messages[2:])


async def test_every_attempt_failing_raises_and_keeps_them_all():
    provider = FakeProvider(responses=["nope", "still nope"])

    with pytest.raises(AgentAttemptsExhaustedError) as excinfo:
        await ScoringAgent(provider, "fake-1").ask(conversation())

    assert excinfo.value.replies == ("nope", "still nope")
    assert len(excinfo.value.problems) == 2
    assert "scorer" in str(excinfo.value)


async def test_max_attempts_of_one_does_not_retry():
    provider = FakeProvider(responses=["nope"])

    with pytest.raises(AgentAttemptsExhaustedError):
        await ScoringAgent(provider, "fake-1", max_attempts=1).ask(conversation())

    assert len(provider.calls) == 1


async def test_max_attempts_must_be_at_least_one():
    with pytest.raises(ValueError, match="max_attempts"):
        ScoringAgent(FakeProvider(), "fake-1", max_attempts=0)


async def test_usage_sums_across_attempts():
    """A retry costs real tokens, and the cost dashboard has to see them."""
    provider = FakeProvider(responses=["nope", '{"verdict": "ok", "score": 1}'])

    result = await ScoringAgent(provider, "fake-1").ask(conversation())

    expected = sum(c.usage.output_tokens for c in result.completions)
    assert result.usage.output_tokens == expected
    assert result.usage.output_tokens > result.completions[0].usage.output_tokens


@dataclass
class RefusingProvider:
    """A provider that declines on policy grounds.

    FakeProvider cannot express this — it only ever stops on `end_turn` or
    `tool_use` — and a refusal is precisely the case a retry must not burn
    attempts on.
    """

    name: str = "refuser"
    calls: list[str] = field(default_factory=list)

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: Sequence[object] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> Completion:
        self.calls.append(model)
        return Completion(content="", stop_reason="refusal", model=model, provider=self.name)

    def stream(self, *args: object, **kwargs: object) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, reachable=True)


async def test_a_refusal_is_not_retried():
    """Asking a model that declined to try again just spends the budget."""
    provider = RefusingProvider()

    with pytest.raises(AgentRefusedError):
        await ScoringAgent(provider, "fake-1").ask(conversation())

    assert len(provider.calls) == 1
