"""Concrete provider adapters.

Each module here translates one vendor's wire format into the types in
:mod:`econometrica.llm.types`, and raises only
:mod:`econometrica.llm.errors` types on failure.
"""

from econometrica.llm.providers.ollama import OllamaProvider

__all__ = ["OllamaProvider"]
