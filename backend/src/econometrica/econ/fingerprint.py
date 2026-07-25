"""Deterministic fingerprints proving two runs saw identical input."""

import hashlib
import json
from typing import Any

import pandas as pd


def fingerprint_frame(df: pd.DataFrame) -> str:
    """SHA-256 over the exact values, column order and index of a frame.

    Uses pandas' own hashing so NaN hashes consistently rather than by identity.
    """
    from pandas.util import hash_pandas_object

    hasher = hashlib.sha256()
    hasher.update("|".join(map(str, df.columns)).encode())
    hasher.update(hash_pandas_object(df, index=True).values.tobytes())
    return hasher.hexdigest()


def fingerprint_params(params: dict[str, Any]) -> str:
    """SHA-256 over canonicalised parameters — key order must not matter."""
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
