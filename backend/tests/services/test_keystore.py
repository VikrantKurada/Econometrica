"""Tests for the encrypted API key store.

The store holds live credentials for five paid providers. The tests that
matter most are the negative ones: that plaintext never reaches disk, and that
a key cannot be read back with the wrong secret.
"""

import json

import pytest

from econometrica.services.keystore import KeyStore, UnreadableKeyStoreError

SECRET = "0123456789abcdef0123456789abcdef"
OTHER_SECRET = "fedcba9876543210fedcba9876543210"
API_KEY = "sk-live-abcdef1234567890-do-not-leak"


@pytest.fixture
def store(tmp_path):
    return KeyStore(path=tmp_path / "keys.enc", secret=SECRET)


def test_round_trips_a_key(store):
    store.set("openai", API_KEY)
    assert store.get("openai") == API_KEY


def test_missing_key_returns_none(store):
    assert store.get("openai") is None


def test_plaintext_never_reaches_disk(store):
    """The whole point of the module."""
    store.set("openai", API_KEY)

    raw = store.path.read_bytes()

    assert API_KEY.encode() not in raw
    assert b"sk-live" not in raw
    assert b"openai" not in raw


def test_stored_file_is_not_readable_json(store):
    """UnicodeDecodeError and JSONDecodeError are both ValueError subclasses."""
    store.set("openai", API_KEY)
    with pytest.raises(ValueError):
        json.loads(store.path.read_bytes().decode("utf-8"))


def test_a_different_secret_cannot_read_the_store(tmp_path):
    path = tmp_path / "keys.enc"
    KeyStore(path=path, secret=SECRET).set("openai", API_KEY)

    with pytest.raises(UnreadableKeyStoreError, match="secret"):
        KeyStore(path=path, secret=OTHER_SECRET).get("openai")


def test_survives_a_reload_from_disk(tmp_path):
    path = tmp_path / "keys.enc"
    KeyStore(path=path, secret=SECRET).set("anthropic", API_KEY)

    assert KeyStore(path=path, secret=SECRET).get("anthropic") == API_KEY


def test_holds_several_providers_independently(store):
    store.set("openai", "key-openai")
    store.set("anthropic", "key-anthropic")
    store.set("gemini", "key-gemini")

    assert store.get("openai") == "key-openai"
    assert store.get("anthropic") == "key-anthropic"
    assert store.get("gemini") == "key-gemini"


def test_overwrites_an_existing_key(store):
    store.set("openai", "old")
    store.set("openai", "new")
    assert store.get("openai") == "new"


def test_delete_removes_a_key(store):
    store.set("openai", API_KEY)
    store.delete("openai")
    assert store.get("openai") is None


def test_deleting_an_absent_key_is_not_an_error(store):
    store.delete("never-set")


def test_configured_lists_provider_names_only(store):
    """The API surfaces this; it must never be able to leak a value."""
    store.set("openai", API_KEY)
    store.set("anthropic", "another")

    configured = store.configured()

    assert set(configured) == {"openai", "anthropic"}
    assert API_KEY not in str(configured)


def test_has_reports_presence_without_returning_the_key(store):
    store.set("openai", API_KEY)
    assert store.has("openai") is True
    assert store.has("gemini") is False


def test_blank_keys_are_rejected(store):
    with pytest.raises(ValueError, match="empty"):
        store.set("openai", "   ")


def test_keys_are_stripped_of_incidental_whitespace(store):
    """Pasting a key from a dashboard commonly drags in a trailing newline."""
    store.set("openai", f"  {API_KEY}\n")
    assert store.get("openai") == API_KEY


def test_a_corrupt_store_raises_rather_than_silently_losing_keys(tmp_path):
    path = tmp_path / "keys.enc"
    KeyStore(path=path, secret=SECRET).set("openai", API_KEY)
    path.write_bytes(b"this is not ciphertext")

    with pytest.raises(UnreadableKeyStoreError):
        KeyStore(path=path, secret=SECRET).get("openai")


def test_reading_an_absent_file_is_empty_not_an_error(tmp_path):
    assert KeyStore(path=tmp_path / "nope.enc", secret=SECRET).configured() == []


def test_two_stores_with_the_same_secret_derive_the_same_fernet_key(tmp_path):
    """Key derivation must be deterministic or a restart loses every key."""
    a = tmp_path / "a.enc"
    KeyStore(path=a, secret=SECRET).set("openai", API_KEY)
    assert KeyStore(path=a, secret=SECRET).get("openai") == API_KEY


def test_repr_does_not_leak_the_secret(store):
    store.set("openai", API_KEY)
    assert SECRET not in repr(store)
    assert API_KEY not in repr(store)
