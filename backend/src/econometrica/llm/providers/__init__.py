"""Concrete provider adapters.

Each module here translates one vendor's wire format into the types in
:mod:`econometrica.llm.types`, and raises only
:mod:`econometrica.llm.errors` types on failure.
"""

from econometrica.llm.providers.anthropic import AnthropicProvider
from econometrica.llm.providers.gemini import GeminiProvider
from econometrica.llm.providers.nvidia import NvidiaProvider
from econometrica.llm.providers.ollama import OllamaProvider
from econometrica.llm.providers.openai import OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "GeminiProvider",
    "NvidiaProvider",
    "OllamaProvider",
    "OpenAIProvider",
]
