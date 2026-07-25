"""Encrypted at-rest storage for provider API keys.

These are live credentials for paid services, so the file on disk reveals
nothing: not the key, not which providers are configured. The whole mapping is
encrypted as one blob rather than value-by-value, because the set of provider
names is itself worth hiding.

The encryption key is derived from ``ECONOMETRICA_SECRET_KEY``. Losing or
changing that secret makes the store unreadable — deliberately. A store that
silently returned nothing after a secret change would look identical to "no
keys configured", and the user would re-enter every credential without ever
learning why.
"""

import base64
import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

#: Fixed salt. A random per-store salt would be strictly better, but it has to
#: live somewhere, and the only available somewhere is next to the ciphertext
#: in a single-user local application — which buys nothing. The threat model
#: here is a stray backup or a shared screen, not an attacker with the file and
#: unlimited time.
_SALT = b"econometrica-keystore-v1"
_ITERATIONS = 480_000


class KeyStoreError(Exception):
    """Base for key store failures."""


class UnreadableKeyStoreError(KeyStoreError):
    """The store exists but cannot be decrypted."""


class KeyStore:
    """A provider-name to API-key mapping, encrypted on disk."""

    def __init__(self, path: Path, secret: str) -> None:
        self.path = Path(path)
        self._fernet = Fernet(_derive_key(secret))

    def get(self, provider: str) -> str | None:
        return self._load().get(provider)

    def has(self, provider: str) -> bool:
        return provider in self._load()

    def configured(self) -> list[str]:
        """Provider names that hold a key. Never returns key material."""
        return sorted(self._load())

    def set(self, provider: str, api_key: str) -> None:
        cleaned = api_key.strip()
        if not cleaned:
            raise ValueError(
                f"keystore: refusing to store an empty key for {provider!r}; "
                "use delete() to remove one"
            )
        keys = self._load()
        keys[provider] = cleaned
        self._save(keys)

    def delete(self, provider: str) -> None:
        keys = self._load()
        if keys.pop(provider, None) is not None:
            self._save(keys)

    def __repr__(self) -> str:
        """Deliberately says nothing about the secret or the keys."""
        return f"KeyStore(path={self.path!s}, providers={len(self._load())})"

    # --- internals ----------------------------------------------------------

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            plaintext = self._fernet.decrypt(self.path.read_bytes())
        except InvalidToken as exc:
            raise UnreadableKeyStoreError(
                f"cannot decrypt {self.path}: the file is corrupt, or it was "
                "written with a different ECONOMETRICA_SECRET_KEY than the "
                "current secret. Restore the original secret, or delete the "
                "file and re-enter the keys."
            ) from exc
        data: Any = json.loads(plaintext)
        if not isinstance(data, dict):
            raise UnreadableKeyStoreError(f"{self.path} did not contain a key mapping")
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, keys: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        token = self._fernet.encrypt(json.dumps(keys).encode("utf-8"))
        self.path.write_bytes(token)


def _derive_key(secret: str) -> bytes:
    """PBKDF2 the configured secret into a Fernet key.

    Deterministic: the same secret must always derive the same key, or a
    restart would orphan every stored credential.
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=_SALT, iterations=_ITERATIONS
    )
    return base64.urlsafe_b64encode(kdf.derive(secret.encode("utf-8")))
