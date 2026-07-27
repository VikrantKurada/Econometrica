"""Chunking documents into pgvector, and finding them again within a project.

Two properties matter more than retrieval quality, and both are structural
rather than conventional.

**Scope is a security property.** Every query filters on `project_id`, and that
column is on the chunk itself — see `db/models/document.py`. A join can be
forgotten; a filter on the row being ranked cannot.

**Retrieved text never becomes a number.** The grounding gate admits only values
a tool computed, and a document is not a tool. A narration citing a figure it
read in a PDF is exactly as ungrounded as one it invented, and retrieval must
not become a side channel into the one mechanical anti-hallucination check the
system has. Nothing here touches `allowed_values`, and a test asserts the gate
still blocks a figure that appears verbatim in a retrieved passage.

Chunks break on sentence boundaries because a chunk cut mid-sentence retrieves
as a fragment that reads like a claim with its qualifier removed.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from econometrica.db.models import Document, DocumentChunk
from econometrica.db.models.document import EMBEDDING_DIMENSIONS

#: Characters per chunk, and how much consecutive chunks share. The overlap is
#: what stops an answer that straddles a boundary from being invisible to both
#: sides of it.
DEFAULT_CHUNK_SIZE = 1200
DEFAULT_OVERLAP = 200

#: How many passages a query returns unless told otherwise. Small on purpose:
#: retrieval feeds a prompt, and a prompt full of near-duplicates crowds out
#: the results the run actually computed.
DEFAULT_LIMIT = 5

#: Where one sentence ends and the next begins: a terminator *followed by
#: whitespace*. The whitespace is what makes it a boundary — without it, the
#: decimal point in "1.8100" reads as the end of a sentence.
#:
#: A `split` rather than a `findall`, because a pattern that matches sentences
#: can fail to match some of the text and drop it. The first version here did
#: exactly that: "The published beta is 1.8100." came back as "8100.", losing
#: the sentence and keeping a number torn out of its context — which is the
#: worst possible thing for a retrieval corpus to do.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


class EmbeddingError(RuntimeError):
    """A document could not be embedded, with a reason a user can act on."""


class Embedder(Protocol):
    """Turns text into vectors.

    Narrow on purpose rather than a method on `LLMProvider`: only one provider
    here serves embeddings, and widening the provider protocol would mean five
    adapters implementing something four of them cannot do.
    """

    model: str
    dimensions: int

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One vector per input, in the order given."""
        ...


@dataclass(frozen=True)
class Retrieved:
    """One passage, and where it came from."""

    document_id: UUID
    document_name: str
    ordinal: int
    text: str
    #: 0 to 1, from cosine distance. Comparable within one query only.
    score: float


def chunk_text(
    text: str, *, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP
) -> list[str]:
    """Split text into overlapping chunks that end on sentence boundaries."""
    sentences = [part.strip() for part in _SENTENCE_BREAK.split(text.strip())]
    sentences = [sentence for sentence in sentences if sentence]
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    length = 0

    for sentence in sentences:
        # A sentence longer than the whole budget is emitted oversized rather
        # than split mid-word: refusing it would drop the content, and cutting
        # it would corrupt it.
        if current and length + len(sentence) + 1 > size:
            chunks.append(" ".join(current))
            current, length = _carry_over(current, overlap)
        current.append(sentence)
        length += len(sentence) + 1

    if current:
        chunks.append(" ".join(current))
    return chunks


def _carry_over(sentences: list[str], overlap: int) -> tuple[list[str], int]:
    """The tail of a chunk that begins the next one."""
    if overlap <= 0:
        return [], 0

    carried: list[str] = []
    length = 0
    for sentence in reversed(sentences):
        if length + len(sentence) + 1 > overlap:
            break
        carried.insert(0, sentence)
        length += len(sentence) + 1
    return carried, length


async def ingest_document(
    session: AsyncSession,
    *,
    project_id: UUID,
    name: str,
    text: str,
    embedder: Embedder,
    size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> Document:
    """Chunk, embed and store a document.

    Embedding failures surface **here**, while the user is still looking at the
    file and can do something about it — not at the first query weeks later.
    """
    chunks = chunk_text(text, size=size, overlap=overlap)
    if not chunks:
        raise EmbeddingError(f"{name} has no text to index")

    try:
        vectors = await embedder.embed(chunks)
    except Exception as exc:
        raise EmbeddingError(f"{name} could not be embedded: {exc}") from exc

    if len(vectors) != len(chunks):
        raise EmbeddingError(
            f"{name}: the embedder returned {len(vectors)} vectors for"
            f" {len(chunks)} chunks"
        )

    document = Document(
        project_id=project_id,
        name=name,
        fingerprint=hashlib.sha256(text.encode()).hexdigest(),
        chars=len(text),
        chunks_count=len(chunks),
    )
    session.add(document)
    await session.flush()

    session.add_all(
        DocumentChunk(
            document_id=document.id,
            ordinal=ordinal,
            project_id=project_id,
            text=chunk,
            embedding=_padded(vector),
            embedding_model=embedder.model,
        )
        for ordinal, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    )
    await session.flush()
    return document


async def retrieve(
    session: AsyncSession,
    *,
    project_id: UUID,
    query: str,
    embedder: Embedder,
    limit: int = DEFAULT_LIMIT,
) -> list[Retrieved]:
    """The closest passages **within one project**."""
    try:
        vectors = await embedder.embed([query])
    except Exception as exc:
        raise EmbeddingError(f"the query could not be embedded: {exc}") from exc

    target = _padded(vectors[0])
    distance = DocumentChunk.embedding.cosine_distance(target)

    rows = await session.execute(
        select(DocumentChunk, Document.name, distance.label("distance"))
        .join(Document, Document.id == DocumentChunk.document_id)
        # The scope filter, on the row being ranked. See the module docstring.
        .where(DocumentChunk.project_id == project_id)
        # Vectors from another model are not comparable with these ones.
        .where(DocumentChunk.embedding_model == embedder.model)
        .order_by(distance)
        .limit(limit)
    )

    return [
        Retrieved(
            document_id=chunk.document_id,
            document_name=name,
            ordinal=chunk.ordinal,
            text=chunk.text,
            # Cosine distance runs 0..2; the useful half is 0..1 and a score
            # below zero would mean opposed vectors, which is not a near miss.
            score=max(0.0, 1.0 - float(distance_value)),
        )
        for chunk, name, distance_value in rows.all()
    ]


def _padded(vector: Sequence[float], dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A vector at the schema's width.

    The schema's, not the embedder's: the column is a fixed-width `Vector`, and
    a vector of any other length is rejected by Postgres rather than coerced.

    Padding a short vector is safe — the extra axes are zero for every chunk and
    for every query, so they cancel. A *longer* vector is refused rather than
    truncated: a truncated embedding is a plausible vector pointing somewhere
    else, and it would rank confidently.
    """
    values = [float(value) for value in vector]
    if len(values) > dimensions:
        raise EmbeddingError(
            f"the embedding model returns {len(values)} dimensions but this"
            f" database stores {dimensions}; a wider model needs a migration"
            " rather than a truncation"
        )
    return values + [0.0] * (dimensions - len(values))


def as_context(hits: Sequence[Retrieved]) -> str:
    """Retrieved passages as prompt text, each attributed.

    Attribution is not decoration: §9 asks retrieval to be attributed in the
    trace, and a passage whose source cannot be named is a quotation from
    nowhere. The header also marks the text as *read* rather than *computed*,
    which is the distinction the grounding gate enforces mechanically.
    """
    if not hits:
        return ""
    blocks = [
        f"[{hit.document_name} #{hit.ordinal}]\n{hit.text}" for hit in hits
    ]
    return (
        "# Retrieved context — read from documents, not computed.\n"
        "Nothing here is a result. Do not cite a number from it.\n\n"
        + "\n\n".join(blocks)
    )
