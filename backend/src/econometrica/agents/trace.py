"""What a run did, recorded step by step.

Kept separate from the database layer on purpose: the orchestrator builds
these, `services.tracing` persists them, and neither has to know about the
other. An agent module importing SQLAlchemy would be the same mistake as a
tool module leaking a statsmodels result.

**One record per completion, not per agent.** An agent that had its first
reply rejected made two calls and was billed for two, so the rejected one is a
step in its own right. Folding it into a single "planner" row would make the
cost dashboard understate every run that needed a retry — which, against a
real local model, was every run until the prompt was fixed.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from econometrica.econ.fingerprint import fingerprint_params
from econometrica.llm.types import Usage

StepKind = Literal["llm", "tool"]
StepState = Literal["ok", "refused", "failed", "skipped"]

#: Roles that can appear in a trace. `econometrician` and `data_steward` run no
#: model, so their steps are `tool` kind with no provider.
AGENTS = (
    "planner",
    "data_steward",
    "econometrician",
    "validator",
    "narrator",
)


def tool_call_hash(tool: str, params: dict[str, Any]) -> str:
    """A stable identity for one tool invocation.

    The tool name is hashed with the parameters rather than beside them, so
    two different tools that happen to take the same arguments do not collide
    — which `adf` and `kpss` routinely would.
    """
    return fingerprint_params({"tool": tool, "params": params})


class StepRecord(BaseModel):
    """One unit of work inside a run."""

    agent: str
    kind: StepKind
    status: StepState
    #: Index into the run's own step list — the DAG edge. None for a root.
    #: An index rather than an id because nothing has been persisted yet.
    parent: int | None = None
    provider: str | None = None
    model: str | None = None
    #: Which try this was within the agent's retry loop, from 1.
    attempt: int = 1
    usage: Usage = Field(default_factory=Usage)
    latency_ms: float = 0.0
    tool: str | None = None
    tool_call_hash: str | None = None
    detail: str = ""
    #: What this attempt was sent and what came back. §8 of the design asks a
    #: step to record them, and without them a trace can say which model ran
    #: but not what it was asked or what it decided — most of the question the
    #: Trace artifact exists to answer. Truncated at `PROMPT_LIMIT`.
    prompt: str = ""
    response: str = ""


class TraceBuilder:
    """Accumulates a run's steps and hands back their positions."""

    def __init__(self) -> None:
        self.records: list[StepRecord] = []

    @property
    def last(self) -> int | None:
        """The most recent step, or None when nothing has run yet."""
        return len(self.records) - 1 if self.records else None

    def add(self, record: StepRecord) -> int:
        self.records.append(record)
        return len(self.records) - 1

    def add_agent_turn(
        self,
        result: Any,
        *,
        agent: str,
        provider: str | None,
        model: str,
        parent: int | None = None,
        final_status: StepState = "ok",
        final_detail: str = "",
    ) -> int:
        """Record every completion an agent made, and return the last index.

        Rejected attempts chain to their predecessor, so the trace shows a
        retry as what it is: a second call that followed a first, not a
        separate branch of work.

        ``final_status`` covers the case where the last attempt parsed but was
        still not usable — a Narrator draft withheld by the grounding gate is
        `refused`, not `ok`, and the difference is the point of the gate.
        """
        last = parent
        prompts = getattr(result, "prompts", ()) or ()
        final = len(result.completions) - 1
        for index, completion in enumerate(result.completions):
            is_final = index == final
            last = self.add(
                StepRecord(
                    agent=agent,
                    kind="llm",
                    status=final_status if is_final else "failed",
                    parent=last,
                    provider=provider,
                    model=model,
                    attempt=index + 1,
                    usage=completion.usage,
                    latency_ms=completion.latency_ms,
                    detail=final_detail if is_final else "reply rejected; retried",
                    # Paired by position: a retry is a different conversation,
                    # so reusing the first prompt would misreport it.
                    prompt=prompts[index] if index < len(prompts) else "",
                    response=completion.content,
                )
            )
        return last if last is not None else self.add(_placeholder(agent, model))


def _placeholder(agent: str, model: str) -> StepRecord:
    """An agent that returned without a single completion.

    Not reachable through the normal path — `ask` always records at least one
    — but a trace that silently drops a turn is worse than one carrying an
    obviously empty step.
    """
    return StepRecord(agent=agent, kind="llm", status="failed", model=model)
