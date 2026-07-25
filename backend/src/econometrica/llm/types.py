"""Vendor-neutral types for the LLM layer.

Five providers with five different wire formats sit behind these types.
Nothing above this module — agents, orchestrator, API, telemetry — should ever
import a vendor SDK type, exactly as nothing above the econ tool boundary
imports a statsmodels result object.
"""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """A model's request to invoke one tool.

    ``id`` is the provider's correlation handle: the tool result must carry it
    back so the model can match answer to question. Providers that do not issue
    one (older completion APIs) get a synthesised id from their adapter.
    """

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolSpec(BaseModel):
    """A tool offered to the model.

    Field names match what ``econ.registry.to_tool_schemas()`` already emits,
    so the registry can be handed to a provider without a translation step.
    """

    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None

    @model_validator(mode="after")
    def tool_results_must_be_attributable(self) -> Self:
        if self.role is Role.TOOL and not self.tool_call_id:
            raise ValueError(
                "a tool result needs the tool_call_id it answers; without it the "
                "provider cannot match the result to its call"
            )
        return self

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(
        cls, content: str, tool_calls: list[ToolCall] | None = None
    ) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> "Message":
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id)


class Capabilities(BaseModel):
    """What a model can actually do.

    The orchestrator reads this before assigning a role: a Validator on a model
    without tool calling, or a Planner on one with a 4k window, fails in ways
    that are much cheaper to catch here than at run time.
    """

    tool_calling: bool = False
    json_mode: bool = False
    streaming: bool = True
    vision: bool = False
    context_window: int = 8192


class ModelInfo(BaseModel):
    id: str
    name: str = ""
    capabilities: Capabilities = Field(default_factory=Capabilities)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Completion(BaseModel):
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    stop_reason: str | None = None
    latency_ms: float = 0.0


class StreamChunk(BaseModel):
    """One increment of a streamed reply.

    Only the final chunk (``done=True``) carries usage, stop reason and any
    tool calls: no provider reports those reliably before the turn ends.
    """

    delta: str = ""
    done: bool = False
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage | None = None
    stop_reason: str | None = None


class ProviderHealth(BaseModel):
    provider: str
    reachable: bool
    detail: str = ""
    models_available: int = 0
