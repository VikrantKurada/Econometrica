"""Provider-agnostic LLM layer.

Vendor wire formats stop here. Everything above — agents, orchestrator, API,
telemetry — speaks only the types in :mod:`econometrica.llm.types`.
"""

from econometrica.llm.base import LLMProvider
from econometrica.llm.errors import (
    ModelNotFoundError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from econometrica.llm.types import (
    Capabilities,
    Completion,
    Message,
    ModelInfo,
    ProviderHealth,
    Role,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)

__all__ = [
    "Capabilities",
    "Completion",
    "LLMProvider",
    "Message",
    "ModelInfo",
    "ModelNotFoundError",
    "ProviderAuthError",
    "ProviderError",
    "ProviderHealth",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderUnavailableError",
    "Role",
    "StreamChunk",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
