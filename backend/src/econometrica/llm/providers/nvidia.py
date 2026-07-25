"""NVIDIA NIM adapter.

NIM serves open-weight models (Llama, Mistral, Nemotron and friends) behind an
OpenAI-compatible endpoint, so it needs no wire handling of its own — only a
different host, credential and name.
"""

from econometrica.llm.providers.openai_compatible import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    name = "nvidia"
    base_url = "https://integrate.api.nvidia.com/v1"
    default_context_window = 128_000
