"""Documents chunked for retrieval, and the vectors that find them.

**Scope is a column on the chunk, not a join.** `project_id` is denormalised
onto `document_chunks` so a retrieval query filters on the same row it searches.
A join can be forgotten in one query and the leak is another project's
documents; a `WHERE` on the row being ranked cannot be.

**Each chunk records the model that embedded it.** Vectors from different models
are not comparable — 384 dimensions from `all-minilm` mean nothing against 1024
from `bge-m3` — so a chunk that did not say would be silently searched against
the wrong space and return confident nonsense.
"""

from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from econometrica.db.base import Base, TimestampedBase

#: Fixed at the schema, because a pgvector index needs a known width. 384 is
#: `all-minilm`, which is small, fast and the likeliest thing to be pulled
#: already. A model of another width is refused rather than truncated: a
#: truncated embedding is a plausible vector pointing somewhere else.
EMBEDDING_DIMENSIONS = 384


class Document(TimestampedBase):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_documents_name_not_blank"),
        CheckConstraint("chunks_count >= 0", name="ck_documents_chunks_non_negative"),
    )

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(500))
    #: Of the text, so the same document uploaded twice is recognisable.
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    chars: Mapped[int] = mapped_column(Integer, default=0)
    chunks_count: Mapped[int] = mapped_column(Integer, default=0)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentChunk(Base):
    """One passage, and where it points in the embedding space."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="ck_document_chunks_ordinal_non_negative"),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    #: Position in the document, so a hit can be shown in context.
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: Denormalised on purpose — see the module docstring. This is the scope
    #: filter, and it has to be on the row being ranked.
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )

    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    #: Which model produced `embedding`. Comparing across models is meaningless.
    embedding_model: Mapped[str] = mapped_column(String(100), default="")

    document: Mapped["Document"] = relationship(back_populates="chunks")
