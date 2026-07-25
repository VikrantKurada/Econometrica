"""Wire schemas for a multi-agent run."""

from datetime import datetime
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


class RunDetail(RunRead):
    """One run with its whole step DAG."""

    steps: list[StepRead] = Field(default_factory=list)
