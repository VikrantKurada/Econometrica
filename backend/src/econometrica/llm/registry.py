"""The set of providers this application can talk to.

One place that knows every provider's name, display label, whether it needs a
credential, and how to construct it from one. The API layer and the Phase 4
orchestrator both read from here rather than importing adapters directly, so
adding a provider means adding one entry.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from econometrica.llm.base import LLMProvider
from econometrica.llm.providers import (
    AnthropicProvider,
    GeminiProvider,
    NvidiaProvider,
    OllamaProvider,
    OpenAIProvider,
)
from econometrica.services.keystore import KeyStore

#: Builds a provider from an API key (empty string when none is needed).
ProviderFactory = Callable[[str], Any]


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    requires_key: bool
    #: Where to get a key, shown in the settings UI next to the input.
    key_url: str = ""


SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="ollama",
        label="Ollama",
        # Runs on the user's own machine. Requiring a key would break the
        # zero-configuration path this application is designed around.
        requires_key=False,
    ),
    ProviderSpec(
        name="anthropic",
        label="Anthropic",
        requires_key=True,
        key_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        name="openai",
        label="OpenAI",
        requires_key=True,
        key_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        name="gemini",
        label="Google Gemini",
        requires_key=True,
        key_url="https://aistudio.google.com/apikey",
    ),
    ProviderSpec(
        name="nvidia",
        label="NVIDIA NIM",
        requires_key=True,
        key_url="https://build.nvidia.com/",
    ),
)

DEFAULT_FACTORIES: dict[str, ProviderFactory] = {
    "ollama": lambda api_key: OllamaProvider(),
    "anthropic": lambda api_key: AnthropicProvider(api_key=api_key),
    "openai": lambda api_key: OpenAIProvider(api_key=api_key),
    "gemini": lambda api_key: GeminiProvider(api_key=api_key),
    "nvidia": lambda api_key: NvidiaProvider(api_key=api_key),
}


@dataclass
class ProviderRegistry:
    """Constructs configured providers on demand.

    ``factories`` is injectable so tests can substitute fakes without any
    network access, and so a future deployment could swap an adapter without
    touching the routers.
    """

    keystore: KeyStore
    factories: dict[str, ProviderFactory] = field(
        default_factory=lambda: dict(DEFAULT_FACTORIES)
    )
    _specs: dict[str, ProviderSpec] = field(
        default_factory=lambda: {spec.name: spec for spec in SPECS}, init=False
    )

    def names(self) -> list[str]:
        return [spec.name for spec in SPECS]

    def specs(self) -> tuple[ProviderSpec, ...]:
        return SPECS

    def spec(self, name: str) -> ProviderSpec:
        if name not in self._specs:
            raise KeyError(f"unknown provider: {name!r}")
        return self._specs[name]

    def is_configured(self, name: str) -> bool:
        """Whether this provider has everything it needs to be used."""
        spec = self.spec(name)
        return not spec.requires_key or self.keystore.has(name)

    def build(self, name: str) -> LLMProvider:
        spec = self.spec(name)
        api_key = self.keystore.get(name) or "" if spec.requires_key else ""
        factory = self.factories.get(name)
        if factory is None:
            raise KeyError(f"no factory registered for provider {name!r}")
        provider: LLMProvider = factory(api_key)
        return provider
