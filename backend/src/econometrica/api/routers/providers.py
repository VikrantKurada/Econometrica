"""Provider configuration and status.

Two rules shape this router:

* **Keys go in, never out.** There is no route that returns key material, and
  no response schema that could carry it. That is a structural guarantee, not
  a convention someone has to remember.
* **A dead provider must not break the page.** Probes run concurrently and
  every failure is caught and reported as status, so one unreachable provider
  cannot take down the settings screen for the other four.
"""

import asyncio

from fastapi import APIRouter, HTTPException, status

from econometrica.api.deps import ProviderRegistryDep
from econometrica.llm.registry import ProviderRegistry, ProviderSpec
from econometrica.schemas.provider import ApiKeyWrite, ModelRead, ProviderStatus

router = APIRouter(prefix="/api/providers", tags=["providers"])

#: A health probe is a courtesy, not the point of the request. Cap it so a
#: hanging provider cannot stall the settings page.
PROBE_TIMEOUT_SECONDS = 5.0


@router.get("", response_model=list[ProviderStatus])
async def list_providers(registry: ProviderRegistryDep) -> list[ProviderStatus]:
    """Every supported provider, with configuration and reachability."""
    return list(
        await asyncio.gather(*(_status(registry, spec) for spec in registry.specs()))
    )


@router.get("/{name}/models", response_model=list[ModelRead])
async def list_models(name: str, registry: ProviderRegistryDep) -> list[ModelRead]:
    spec = _spec_or_404(registry, name)

    if not registry.is_configured(name):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{spec.label} has no api key configured",
        )

    try:
        models = await registry.build(name).list_models()
    except Exception as exc:
        # Adapters raise only ProviderError, but a status route must not 500 on
        # an adapter bug either.
        detail = getattr(exc, "detail", None) or str(exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{spec.label} is unavailable: {detail}",
        ) from exc

    return [
        ModelRead(
            id=model.id,
            name=model.name or model.id,
            capabilities=model.capabilities.model_dump(),
        )
        for model in models
    ]


@router.put("/{name}/key", response_model=ProviderStatus)
async def set_api_key(
    name: str, payload: ApiKeyWrite, registry: ProviderRegistryDep
) -> ProviderStatus:
    """Store a key. The response reports status only — never the key."""
    spec = _spec_or_404(registry, name)

    if not spec.requires_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{spec.label} runs locally and takes no api key",
        )

    registry.keystore.set(name, payload.api_key)
    return await _status(registry, spec)


@router.delete("/{name}/key", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(name: str, registry: ProviderRegistryDep) -> None:
    """Remove a stored key. Absent keys delete cleanly, so this is idempotent."""
    _spec_or_404(registry, name)
    registry.keystore.delete(name)


# --- internals --------------------------------------------------------------


def _spec_or_404(registry: ProviderRegistry, name: str) -> ProviderSpec:
    try:
        return registry.spec(name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown provider {name!r}"
        ) from exc


async def _status(registry: ProviderRegistry, spec: ProviderSpec) -> ProviderStatus:
    configured = registry.is_configured(spec.name)
    result = ProviderStatus(
        name=spec.name,
        label=spec.label,
        requires_key=spec.requires_key,
        key_url=spec.key_url,
        configured=configured,
        reachable=False,
    )
    if not configured:
        # Skip the probe entirely: an unconfigured provider would fail anyway,
        # and there is no point paying network latency to learn that.
        result.detail = f"no api key configured for {spec.label}"
        return result

    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            health = await registry.build(spec.name).health()
    except TimeoutError:
        result.detail = f"{spec.label} did not respond within {PROBE_TIMEOUT_SECONDS:g}s"
        return result
    except Exception as exc:
        # health() is contractually non-raising, but a third-party adapter bug
        # must degrade to "unreachable", not a 500 for the whole listing.
        result.detail = str(getattr(exc, "detail", None) or exc)
        return result

    result.reachable = health.reachable
    result.detail = health.detail
    result.models_available = health.models_available
    return result
