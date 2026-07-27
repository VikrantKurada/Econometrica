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


# --- what the trace can show afterwards ---------------------------------------
#
# §8 of the design says a step records "the agent, provider, model, prompt and
# response". The first three were captured from Phase 4; the last two were not,
# so a trace viewer could say which model ran and not what it was asked or what
# it said — which is most of the question the Trace artifact exists to answer.


async def test_the_result_carries_the_prompt_and_the_reply():
    reply = '{"verdict": "ok", "score": 5}'
    provider = FakeProvider(responses=[reply])

    result = await ScoringAgent(provider, "fake-1").ask([Message.user("score this")])

    assert "score this" in result.prompts[0]
    assert result.completions[0].content == reply


async def test_each_attempt_carries_the_prompt_it_was_actually_sent():
    """A retry is a different conversation — it carries the rejected reply and
    the problem. Pairing every attempt with the first prompt would misreport
    what the model was asked the second time."""
    provider = FakeProvider(responses=["not json", '{"verdict": "ok", "score": 5}'])

    result = await ScoringAgent(provider, "fake-1").ask([Message.user("score")])

    assert len(result.prompts) == 2
    assert "not json" in result.prompts[1]
    assert "not json" not in result.prompts[0]


async def test_a_very_long_prompt_is_truncated_rather_than_stored_whole():
    """The planner's prompt carries the whole tool catalogue. Stored verbatim
    per attempt it would dominate the database for a trace nobody reads in
    full, so it is cut with a marker that says so."""
    from econometrica.agents.base import PROMPT_LIMIT

    provider = FakeProvider(responses=['{"verdict": "ok", "score": 5}'])
    huge = "x" * (PROMPT_LIMIT + 5_000)

    result = await ScoringAgent(provider, "fake-1").ask([Message.user(huge)])

    assert len(result.prompts[0]) <= PROMPT_LIMIT + 100
    assert "truncated" in result.prompts[0]


async def test_the_prompts_of_a_failed_agent_survive():
    """The case where a trace is most worth reading."""
    provider = FakeProvider(responses=["not json"] * 2)

    with pytest.raises(AgentAttemptsExhaustedError) as raised:
        await ScoringAgent(provider, "fake-1").ask([Message.user("score")])

    assert len(raised.value.prompts) == 2
