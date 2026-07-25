"""Provider failures, normalised.

A caller should be able to catch :class:`ProviderError` and handle any vendor's
failure without knowing which vendor it was. Every subclass names the provider,
because "connection refused" is useless in a five-provider application.
"""


class ProviderError(Exception):
    """Base for every provider failure."""

    def __init__(self, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


class ProviderUnavailableError(ProviderError):
    """The provider could not be reached — daemon down, network, timeout."""


class ProviderAuthError(ProviderError):
    """Credentials missing, malformed or rejected."""


class ProviderRateLimitError(ProviderError):
    """Rate limited or over quota."""

    def __init__(self, provider: str, detail: str, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(provider, detail)


class ModelNotFoundError(ProviderError):
    """The requested model is not available from this provider."""

    def __init__(self, provider: str, model: str) -> None:
        self.model = model
        super().__init__(provider, f"unknown model {model!r}")


class ProviderResponseError(ProviderError):
    """The provider replied, but not with something we can use."""
