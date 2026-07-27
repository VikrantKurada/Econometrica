"""Dependencies and lookup helpers shared by every router."""

from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption

from econometrica.agents.data_steward import FactorSource
from econometrica.config import get_settings
from econometrica.data.base import PriceSource
from econometrica.data.famafrench import FamaFrenchFactorSource
from econometrica.data.registry import RATE_SOURCE, build_price_source
from econometrica.db.models import Chat, Project
from econometrica.db.session import get_session
from econometrica.llm.registry import ProviderRegistry
from econometrica.services.keystore import KeyStore

# Declared as an ``Annotated`` alias rather than a ``Depends()`` default so that
# route signatures stay free of mutable-looking defaults and the tests can
# override ``get_session`` in one place.
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@lru_cache(maxsize=1)
def get_keystore() -> KeyStore:
    """The encrypted API key store, derived from settings.

    Cached because the Fernet key derivation is deliberately expensive
    (480,000 PBKDF2 rounds); redoing it per request would show up as latency
    on every provider call.
    """
    settings = get_settings()
    return KeyStore(path=settings.storage_dir / "keys.enc", secret=settings.secret_key)


def get_provider_registry() -> ProviderRegistry:
    return ProviderRegistry(keystore=get_keystore())


ProviderRegistryDep = Annotated[ProviderRegistry, Depends(get_provider_registry)]


def get_price_source() -> PriceSource:
    """Where a run's market data comes from.

    `data/registry.py` knows every source and whether it is cached; this only
    reads the setting. The cache lives under the storage directory because it
    has to be somewhere a user can delete without consequence.
    """
    settings = get_settings()
    return build_price_source(
        settings.price_source, cache_root=settings.storage_dir / "prices"
    )


PriceSourceDep = Annotated[PriceSource, Depends(get_price_source)]


def get_rate_source() -> PriceSource:
    """Where a plan's risk-free rate comes from.

    Always FRED, and deliberately not a setting: it is the only source here that
    publishes a risk-free rate, it needs no API key, and a price source cannot
    substitute — Yahoo has no `DGS3MO`. Resolved separately from prices for that
    reason, and cached in its own directory so the two do not share keys.

    Unconditional even when the prices are synthetic. A generated-price run that
    asks for a real rate still carries its `synthetic_data` risk flag, and the
    report names the rate series, so nothing about it reads as market data.
    """
    settings = get_settings()
    return build_price_source(RATE_SOURCE, cache_root=settings.storage_dir / "rates")


RateSourceDep = Annotated[PriceSource, Depends(get_rate_source)]


def get_factor_source() -> FactorSource:
    """Where a plan's factor set comes from.

    Ken French, and not a setting for the same reason FRED is not: it is the
    canonical publisher of these factors, needs no API key, and nothing else
    here serves them. Without it `ff3`, `ff5` and `carhart4` cannot run at all.
    """
    return FamaFrenchFactorSource()


FactorSourceDep = Annotated[FactorSource, Depends(get_factor_source)]


def _not_found(entity: str, entity_id: UUID) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail=f"{entity} {entity_id} not found"
    )


async def get_project_or_404(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise _not_found("Project", project_id)
    return project


async def get_chat_or_404(
    session: AsyncSession, chat_id: UUID, options: Sequence[ORMOption] = ()
) -> Chat:
    """Load a chat, optionally with eager loaders.

    Relationships must be loaded up front: touching an unloaded one later in an
    async request raises MissingGreenlet rather than emitting a lazy SELECT.
    """
    loader_options: dict[str, Any] = {"options": list(options)} if options else {}
    chat = await session.get(Chat, chat_id, **loader_options)
    if chat is None:
        raise _not_found("Chat", chat_id)
    return chat
