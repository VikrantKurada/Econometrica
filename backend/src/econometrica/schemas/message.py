"""DTOs for chat messages."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageSend(BaseModel):
    """A user turn, plus which model should answer it.

    Provider and model are chosen per message rather than per chat: the point
    of a five-provider application is switching mid-conversation, and the
    transcript records what actually answered each turn.
    """

    content: str = Field(min_length=1, max_length=100_000)
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=200)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be blank")
        return cleaned


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    chat_id: UUID
    seq: int
    role: str
    content: str
    provider: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_ms: float
    stop_reason: str | None
    error: str | None
    created_at: datetime
