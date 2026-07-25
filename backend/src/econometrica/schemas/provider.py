"""DTOs for the providers API.

Note what is absent: there is no schema anywhere in this module that carries an
API key outward. Keys are write-only by construction, not by discipline.
"""

from pydantic import BaseModel, Field, field_validator


class ProviderStatus(BaseModel):
    """One provider as the settings UI sees it."""

    name: str
    label: str
    requires_key: bool
    key_url: str = ""
    #: Has everything it needs to be used (a key, where one is required).
    configured: bool
    #: Answered its health probe just now. Only probed when configured.
    reachable: bool
    detail: str = ""
    models_available: int = 0


class ApiKeyWrite(BaseModel):
    """Inbound only — deliberately has no read counterpart."""

    api_key: str = Field(min_length=1, max_length=500)

    @field_validator("api_key")
    @classmethod
    def key_must_not_be_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("api_key must not be blank")
        return cleaned


class ModelCapabilitiesRead(BaseModel):
    tool_calling: bool
    json_mode: bool
    streaming: bool
    vision: bool
    context_window: int


class ModelRead(BaseModel):
    id: str
    name: str
    capabilities: ModelCapabilitiesRead
