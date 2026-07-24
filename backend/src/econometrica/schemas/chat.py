from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class ChatUpdate(BaseModel):
    """A partial update over a chat's name and capability overrides.

    The toggles are three-state and ``None`` is a real value here, not a stand
    in for "not supplied": sending ``null`` clears the override so the chat
    inherits from its project again. Omitting the field leaves it as it is.
    ``name`` is the exception — it is NOT NULL, so an explicit ``null`` is a 422.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    web_search_enabled: bool | None = None
    mcp_enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str | None) -> str:
        if v is None:
            raise ValueError("name must not be null; omit it to leave it unchanged")
        if not v.strip():
            raise ValueError("name must not be blank")
        return v.strip()


class ChatRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    # ``None`` means "inherit from the project"; see ChatUpdate.
    web_search_enabled: bool | None
    mcp_enabled: bool | None
    created_at: datetime
    updated_at: datetime


class CapabilitiesRead(BaseModel):
    """What a chat can actually do, after project settings and overrides merge.

    This is what the UI renders its toggle states from, so it is deliberately
    flat booleans with nothing left to inherit.
    """

    model_config = ConfigDict(from_attributes=True)

    web_search: bool
    mcp: bool
    code_sandbox: bool
    validation_tier: str
