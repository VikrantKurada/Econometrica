"""The set of price sources this application can use.

One place that knows every source's name, how to construct it, and whether its
fetches go through the on-disk cache — the same shape as `llm/registry.py`, for
the same reason: the API layer names a source rather than importing an adapter,
so adding one means adding an entry here and nothing else.

`ECONOMETRICA_PRICE_SOURCE` takes one of `names()`, and a test asserts the
settings enum and this table agree. They have to: a value that passes settings
validation but has no factory would surface as a 500 on the first run rather
than as a startup failure.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from econometrica.agents.data_steward import PriceSource
from econometrica.data.cache import DEFAULT_MAX_AGE, CachingPriceSource
from econometrica.data.synthetic import SyntheticPriceSource
from econometrica.data.unconfigured import UnconfiguredPriceSource
from econometrica.data.yahoo import YahooPriceSource

SourceFactory = Callable[[], PriceSource]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    #: Human-facing, for a settings UI.
    label: str
    #: Whether fetches go through the on-disk cache. Only sources that reach
    #: the network benefit: the synthetic generator is instant and
    #: deterministic, so caching it would add disk churn to buy nothing, and
    #: the unconfigured source has nothing to cache.
    cached: bool


SPECS: tuple[SourceSpec, ...] = (
    SourceSpec(name="none", label="None configured", cached=False),
    SourceSpec(name="synthetic", label="Synthetic (generated, not market data)", cached=False),
    SourceSpec(name="yahoo", label="Yahoo Finance", cached=True),
)

DEFAULT_FACTORIES: dict[str, SourceFactory] = {
    "none": UnconfiguredPriceSource,
    "synthetic": SyntheticPriceSource,
    "yahoo": YahooPriceSource,
}

_SPECS = {entry.name: entry for entry in SPECS}


def names() -> list[str]:
    return [entry.name for entry in SPECS]


def spec(name: str) -> SourceSpec:
    if name not in _SPECS:
        raise KeyError(f"unknown price source {name!r}; known: {', '.join(names())}")
    return _SPECS[name]


def build_price_source(
    name: str,
    *,
    cache_root: Path,
    max_age: timedelta = DEFAULT_MAX_AGE,
    factories: dict[str, SourceFactory] | None = None,
) -> PriceSource:
    """Construct a source by name, wrapped in the cache where it earns one.

    ``factories`` is injectable for the same reason `ProviderRegistry`'s is: a
    test can substitute a fake without reaching the network.
    """
    entry = spec(name)
    factory = (factories or DEFAULT_FACTORIES).get(name)
    if factory is None:
        raise KeyError(f"no factory registered for price source {name!r}")

    source = factory()
    if not entry.cached:
        return source
    return CachingPriceSource(source, namespace=name, root=cache_root, max_age=max_age)
