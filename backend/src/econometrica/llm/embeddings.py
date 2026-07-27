"""Embeddings from a local Ollama model.

A separate, narrow adapter rather than a method on `LLMProvider`. Only one of
the five providers here serves embeddings without a key, and widening the
protocol would mean four adapters implementing something they cannot do — the
same reason `PriceSource` is its own protocol rather than a method on something
larger.

`all-minilm` by default: 384 dimensions, small enough to be pulled already, and
the width `document_chunks.embedding` is declared at. A wider model needs a
migration, not a truncation — see `services/rag._padded`.
"""

import httpx

from econometrica.config import get_settings

#: 384 dimensions, matching `EMBEDDING_DIMENSIONS`.
DEFAULT_EMBEDDING_MODEL = "all-minilm"

#: Embedding a document is a batch job, not a chat turn, and a cold model load
#: is measured in tens of seconds.
DEFAULT_TIMEOUT = 120.0


class OllamaEmbedder:
    """`POST /api/embed`, which takes a list and returns one vector per input."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        base_url: str = "",
        dimensions: int = 384,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self._base_url = (base_url or get_settings().ollama_base_url).rstrip("/")
        self._timeout = timeout

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/api/embed",
                json={"model": self.model, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()

        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            # A shape that does not match the request is not something to
            # recover from silently: the vectors would be paired with the wrong
            # chunks and every retrieval afterwards would be confidently wrong.
            raise ValueError(
                f"{self.model} returned {len(vectors or [])} embeddings for"
                f" {len(texts)} inputs"
            )
        return [[float(value) for value in vector] for vector in vectors]
