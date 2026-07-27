"""Wire schemas for a multi-agent run."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunStart(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    #: Extra project context for the Planner — instruments, conventions, caveats.
    context: str = Field(default="", max_length=4000)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    parent_id: UUID | None
    agent: str
    kind: str
    status: str
    attempt: int
    provider: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_ms: float
    tool: str | None
    tool_call_hash: str | None
    detail: str
    prompt: str
    response: str
    created_at: datetime


class RunRead(BaseModel):
    """A run without its steps — what a list of runs needs."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chat_id: UUID
    question: str
    status: str
    tier: str
    revisions: int
    error: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_ms: float
    created_at: datetime


class StepReproduction(BaseModel):
    """Whether one recorded step came back the same."""

    step_id: str
    tool: str
    reproduced: bool
    status: str
    original_status: str
    data_fingerprint: str = ""
    original_data_fingerprint: str = ""
    params_hash: str = ""
    original_params_hash: str = ""
    #: What differed, in words. Empty when nothing did.
    detail: str = ""


class RerunReport(BaseModel):
    """The answer to "does this manifest still produce this result?".

    A re-run is not a new analysis: it re-executes the recorded plan against
    freshly resolved data and compares. No model is asked anything, which is
    what makes the answer about the data and the tools rather than about
    whether a model happened to plan the same way twice.
    """

    run_id: UUID
    reproduced: bool
    steps: list[StepReproduction] = Field(default_factory=list)


class RunDetail(RunRead):
    """One run with its whole step DAG, and everything it produced.

    `outcome` is the serialised `RunOutcome`: plan, data quality, results,
    charts, verdict and narration. It is deliberately absent from `RunRead` —
    a result's series live in there, so a list of runs carrying it would make
    listing the sidebar the most expensive request in the app.

    Typed as a bare dict rather than as `RunOutcome` because it is read back
    from JSONB written by a possibly older version of the pipeline, and a
    stored run should stay readable after the shape moves on. The client
    narrows it; the database does not get to fail a GET.
    """

    steps: list[StepRead] = Field(default_factory=list)
    outcome: dict[str, Any] = Field(default_factory=dict)
