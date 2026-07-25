"""What every agent is: a role, a model, and one retry.

The retry loop lives here rather than in each agent because unusable model
output is the ordinary case, not the exceptional one, and because getting it
subtly wrong six different ways is the obvious failure mode of six agents.

Two rules the loop keeps:

* **Show the model its own reply.** A retry that only says "that was invalid"
  reliably gets the same invalid reply back. The rejected text goes into the
  conversation as an assistant turn, followed by the specific problem.
* **Never retry a refusal.** A provider that declined on policy grounds will
  decline again; retrying spends the attempt budget to learn nothing.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from econometrica.agents.schemas import AgentOutputError, parse_agent_json
from econometrica.llm.base import LLMProvider
from econometrica.llm.types import Completion, Message, Usage

#: What the model is told when its last reply could not be used. Deliberately
#: names the problem and repeats the format demand — models that drift into
#: prose drift back without the second half.
RETRY_PROMPT = (
    "That reply could not be used.\n\n{problem}\n\n"
    "Reply again with the corrected JSON object only — no prose, no code fence."
)


class AgentAttemptsExhaustedError(AgentOutputError):
    """Every attempt produced output that could not be used.

    Keeps all of them: which *way* the model failed twice is the difference
    between a bad prompt and a model that cannot do the job, and only the
    caller has the context to tell them apart.
    """

    def __init__(self, role: str, replies: tuple[str, ...], problems: tuple[str, ...]) -> None:
        last = problems[-1] if problems else "no attempts were made"
        super().__init__(
            f"{role}: {len(replies)} attempt(s) produced no usable output; last problem: {last}",
            replies[-1] if replies else "",
        )
        self.role = role
        self.replies = replies
        self.problems = problems


class AgentRefusedError(AgentOutputError):
    """The provider declined the request on policy grounds."""

    def __init__(self, role: str, model: str) -> None:
        super().__init__(f"{role}: {model} refused the request", "")
        self.role = role
        self.model = model


@dataclass(frozen=True)
class AgentResult[OutputT: BaseModel]:
    """One agent's answer, with what it cost to get."""

    output: OutputT
    #: Every completion, including the rejected ones.
    completions: tuple[Completion, ...]

    @property
    def attempts(self) -> int:
        return len(self.completions)

    @property
    def usage(self) -> Usage:
        """Summed across attempts — a retry is billed like any other call."""
        return Usage(
            input_tokens=sum(c.usage.input_tokens for c in self.completions),
            output_tokens=sum(c.usage.output_tokens for c in self.completions),
            cache_read_tokens=sum(c.usage.cache_read_tokens for c in self.completions),
            cache_write_tokens=sum(c.usage.cache_write_tokens for c in self.completions),
        )

    @property
    def latency_ms(self) -> float:
        return sum(c.latency_ms for c in self.completions)


class Agent[OutputT: BaseModel](ABC):
    """One role, bound to one provider and model.

    Roles are bound per project (``Project.model_assignments``), so two agents
    in the same run routinely speak to different vendors — which is the point
    of the Validator.
    """

    role: str = "agent"

    def __init__(self, provider: LLMProvider, model: str, *, max_attempts: int = 2) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self.provider = provider
        self.model = model
        self.max_attempts = max_attempts

    @abstractmethod
    def output_model(self) -> type[OutputT]:
        """The shape this agent's reply must take."""

    async def ask(self, messages: Sequence[Message]) -> AgentResult[OutputT]:
        """Run the conversation until the reply parses, or the budget is spent."""
        conversation = list(messages)
        completions: list[Completion] = []
        problems: list[str] = []

        for attempt in range(1, self.max_attempts + 1):
            completion = await self.provider.complete(
                conversation,
                model=self.model,
                temperature=0.0,
                # Determinism and JSON mode both remove causes of malformed
                # output at the request rather than repairing them after.
                json_mode=True,
            )
            completions.append(completion)

            if completion.refused:
                raise AgentRefusedError(self.role, self.model)

            try:
                payload = parse_agent_json(completion.content)
                output = self.output_model().model_validate(payload)
            except (AgentOutputError, PydanticValidationError) as exc:
                problems.append(str(exc))
                if attempt < self.max_attempts:
                    conversation.append(Message.assistant(completion.content))
                    conversation.append(Message.user(RETRY_PROMPT.format(problem=exc)))
                continue

            return AgentResult(output=output, completions=tuple(completions))

        raise AgentAttemptsExhaustedError(
            self.role,
            tuple(c.content for c in completions),
            tuple(problems),
        )
