from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The validation tiers the orchestrator knows how to run. Anything else is a
# client bug, so it is rejected at the edge rather than stored.
ValidationTier = Literal["single", "critic", "consensus"]


def _strip_or_reject_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("name must not be blank")
    return value.strip()


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str) -> str:
        return _strip_or_reject_blank(v)


class ProjectUpdate(BaseModel):
    """A partial update: every field is optional and only what is sent is applied.

    ``None`` here means "not supplied" for the fields backed by NOT NULL
    columns. Pydantic does not run validators over defaults, so the validators
    below only fire when a client sends an explicit ``null`` — which is a
    request to write NULL into a NOT NULL column and has to be a 422, not a
    database error. ``description`` is genuinely nullable, so an explicit
    ``null`` there is a legitimate way to clear it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    web_search_enabled: bool | None = None
    mcp_enabled: bool | None = None
    code_sandbox_enabled: bool | None = None
    validation_tier: ValidationTier | None = None
    model_assignments: dict[str, Any] | None = None
    #: Sent whole rather than patched. An allowlist edited by delta is one
    #: whose current contents the client has to have guessed right, and this is
    #: the field where guessing wrong grants a permission.
    mcp_servers: list[Any] | None = None
    mcp_allowlist: list[str] | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, v: str | None) -> str:
        if v is None:
            raise ValueError("name must not be null; omit it to leave it unchanged")
        return _strip_or_reject_blank(v)

    @field_validator(
        "web_search_enabled",
        "mcp_enabled",
        "code_sandbox_enabled",
        "validation_tier",
        "model_assignments",
        "mcp_servers",
        "mcp_allowlist",
    )
    @classmethod
    def must_not_be_explicitly_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("field must not be null; omit it to leave it unchanged")
        return v


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    web_search_enabled: bool
    mcp_enabled: bool
    code_sandbox_enabled: bool
    # Deliberately a plain ``str`` and not ``ValidationTier``: reads must not
    # fail on a row that predates the allowed set or was written out of band.
    validation_tier: str
    mcp_servers: list[Any]
    mcp_allowlist: list[str]
    model_assignments: dict[str, Any]
    created_at: datetime
    updated_at: datetime
