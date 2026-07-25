"""Tests for the providers API.

This endpoint is where API keys meet HTTP, so the tests that matter most are
the ones asserting key material never travels outward — not in a list, not in
an error, not in a round-trip echo.
"""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from econometrica.llm.fake import FakeProvider
from econometrica.llm.registry import ProviderRegistry
from econometrica.main import app
from econometrica.services.keystore import KeyStore

SECRET = "0123456789abcdef0123456789abcdef"
LIVE_KEY = "sk-live-super-secret-value-000"


@pytest.fixture
def keystore(tmp_path):
    return KeyStore(path=tmp_path / "keys.enc", secret=SECRET)


@pytest_asyncio.fixture
async def provider_client(keystore):
    """A client whose registry builds fakes instead of touching the network."""
    from econometrica.api.deps import get_provider_registry

    registry = ProviderRegistry(keystore=keystore, factories={})
    registry.factories = {
        name: (lambda api_key, _n=name: FakeProvider(name=_n, reachable=True))
        for name in registry.names()
    }
    app.dependency_overrides[get_provider_registry] = lambda: registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_provider_registry, None)


# --- listing ----------------------------------------------------------------


async def test_lists_every_supported_provider(provider_client):
    response = await provider_client.get("/api/providers")

    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert names == {"ollama", "openai", "nvidia", "anthropic", "gemini"}


async def test_ollama_does_not_require_a_key(provider_client):
    """It runs locally; demanding a key would block the zero-config path."""
    providers = {p["name"]: p for p in (await provider_client.get("/api/providers")).json()}

    assert providers["ollama"]["requires_key"] is False
    assert providers["openai"]["requires_key"] is True


async def test_a_keyed_provider_without_a_key_reports_unconfigured(provider_client):
    providers = {p["name"]: p for p in (await provider_client.get("/api/providers")).json()}

    assert providers["openai"]["configured"] is False
    assert providers["openai"]["reachable"] is False


async def test_setting_a_key_marks_the_provider_configured(provider_client):
    await provider_client.put("/api/providers/openai/key", json={"api_key": LIVE_KEY})

    providers = {p["name"]: p for p in (await provider_client.get("/api/providers")).json()}
    assert providers["openai"]["configured"] is True


async def test_each_provider_carries_a_human_label(provider_client):
    """The UI picker needs a display name; 'nvidia' alone reads badly."""
    providers = {p["name"]: p for p in (await provider_client.get("/api/providers")).json()}
    assert providers["nvidia"]["label"] == "NVIDIA NIM"


# --- key material must never travel outward --------------------------------


async def test_the_listing_never_contains_key_material(provider_client):
    """The single most important test in this file."""
    await provider_client.put("/api/providers/openai/key", json={"api_key": LIVE_KEY})

    response = await provider_client.get("/api/providers")

    assert LIVE_KEY not in response.text
    assert "sk-live" not in response.text


async def test_storing_a_key_does_not_echo_it_back(provider_client):
    response = await provider_client.put(
        "/api/providers/openai/key", json={"api_key": LIVE_KEY}
    )

    assert response.status_code == 200
    assert LIVE_KEY not in response.text
    assert response.json()["configured"] is True


async def test_there_is_no_endpoint_that_returns_a_key(provider_client):
    """A read route would make one misconfigured proxy a credential leak."""
    await provider_client.put("/api/providers/openai/key", json={"api_key": LIVE_KEY})

    response = await provider_client.get("/api/providers/openai/key")
    assert response.status_code in (404, 405)


# --- key management ---------------------------------------------------------


async def test_a_stored_key_reaches_the_keystore(provider_client, keystore):
    await provider_client.put("/api/providers/anthropic/key", json={"api_key": LIVE_KEY})
    assert keystore.get("anthropic") == LIVE_KEY


async def test_a_blank_key_is_rejected(provider_client):
    response = await provider_client.put("/api/providers/openai/key", json={"api_key": "   "})
    assert response.status_code == 422


async def test_deleting_a_key_unconfigures_the_provider(provider_client, keystore):
    await provider_client.put("/api/providers/openai/key", json={"api_key": LIVE_KEY})

    response = await provider_client.delete("/api/providers/openai/key")

    assert response.status_code == 204
    assert keystore.get("openai") is None


async def test_deleting_an_absent_key_is_not_an_error(provider_client):
    response = await provider_client.delete("/api/providers/gemini/key")
    assert response.status_code == 204


async def test_a_key_cannot_be_set_on_a_provider_that_takes_none(provider_client):
    response = await provider_client.put("/api/providers/ollama/key", json={"api_key": "x"})
    assert response.status_code == 400
    assert "ollama" in response.text.lower()


async def test_unknown_provider_returns_404(provider_client):
    assert (await provider_client.get("/api/providers/nope/models")).status_code == 404
    assert (
        await provider_client.put("/api/providers/nope/key", json={"api_key": "x"})
    ).status_code == 404


# --- models -----------------------------------------------------------------


async def test_models_are_listed_for_a_reachable_provider(provider_client):
    await provider_client.put("/api/providers/openai/key", json={"api_key": LIVE_KEY})

    response = await provider_client.get("/api/providers/openai/models")

    assert response.status_code == 200
    body = response.json()
    assert body
    assert "id" in body[0]
    assert "context_window" in body[0]["capabilities"]


async def test_models_for_an_unconfigured_provider_is_a_clean_error(provider_client):
    response = await provider_client.get("/api/providers/openai/models")
    assert response.status_code == 503
    assert "key" in response.text.lower()


# --- probing must not take the endpoint down -------------------------------


async def test_an_unreachable_provider_does_not_fail_the_listing(keystore):
    """One dead provider must not break the settings page for the others."""
    from econometrica.api.deps import get_provider_registry

    keystore.set("openai", LIVE_KEY)
    registry = ProviderRegistry(keystore=keystore, factories={})
    registry.factories = {
        name: (lambda api_key, _n=name: FakeProvider(name=_n, reachable=(_n != "openai")))
        for name in registry.names()
    }
    app.dependency_overrides[get_provider_registry] = lambda: registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/providers")

    app.dependency_overrides.pop(get_provider_registry, None)

    assert response.status_code == 200
    providers = {p["name"]: p for p in response.json()}
    assert providers["openai"]["reachable"] is False
    assert providers["openai"]["detail"]
    assert providers["ollama"]["reachable"] is True


async def test_a_provider_that_raises_on_probe_is_reported_not_propagated(keystore):
    """health() is contractually non-raising, but a bad adapter could break it."""
    from econometrica.api.deps import get_provider_registry

    class Exploding:
        name = "openai"

        async def health(self):
            raise RuntimeError("kaboom")

    registry = ProviderRegistry(keystore=keystore, factories={})
    keystore.set("openai", LIVE_KEY)
    registry.factories = {
        name: (
            (lambda api_key: Exploding())
            if name == "openai"
            else (lambda api_key, _n=name: FakeProvider(name=_n))
        )
        for name in registry.names()
    }
    app.dependency_overrides[get_provider_registry] = lambda: registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/providers")

    app.dependency_overrides.pop(get_provider_registry, None)

    assert response.status_code == 200
    providers = {p["name"]: p for p in response.json()}
    assert providers["openai"]["reachable"] is False


# --- the registry itself ----------------------------------------------------


def test_the_real_registry_builds_every_provider(keystore):
    """Guards against a provider being added without being registered."""
    registry = ProviderRegistry(keystore=keystore)

    for name in registry.names():
        keystore.set(name, "test-key") if registry.spec(name).requires_key else None
        provider = registry.build(name)
        assert provider.name == name


def test_building_an_unknown_provider_raises(keystore):
    with pytest.raises(KeyError, match="nope"):
        ProviderRegistry(keystore=keystore).build("nope")
