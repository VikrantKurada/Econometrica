"""OpenAI adapter."""

from econometrica.llm.providers.openai_compatible import OpenAICompatibleProvider


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    base_url = "https://api.openai.com/v1"
    default_context_window = 128_000
