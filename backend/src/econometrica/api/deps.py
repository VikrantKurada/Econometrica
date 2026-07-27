"""Dependencies and lookup helpers shared by every router."""

from collections.abc import Sequence
from functools import lru_cache
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.interfaces import ORMOption

from econometrica.config import get_settings
from econometrica.data.base import PriceSource
from econometrica.data.registry import build_price_source
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
