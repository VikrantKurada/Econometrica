"""Adding, listing and removing a project's retrieval documents.

Ingest requires the embedder and fails loudly when it is down — a document
cannot be stored without vectors, and there is nothing useful to fall back to
(unlike a *run's* retrieval, which degrades). The route commits: `get_session`
does not, and an ingest that only flushed would be discarded the moment the
request ended while the response reported what it would have stored.
"""

import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from econometrica.api.deps import EmbedderDep, SessionDep, get_project_or_404
from econometrica.db.models import Document
from econometrica.schemas.document import DocumentRead
from econometrica.services.documents import (
    DocumentError,
    EmptyDocumentError,
    UnsupportedDocumentError,
    extract_text,
)
from econometrica.services.rag import EmbeddingError, ingest_document

MAX_DOCUMENT_BYTES = 8 * 1024 * 1024

router = APIRouter(prefix="/api/projects", tags=["documents"])
#: Reading and deleting need only the document id; the project scoped creation.
documents = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post(
    "/{project_id}/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_document(
    project_id: UUID,
    session: SessionDep,
    embedder: EmbedderDep,
    file: Annotated[UploadFile, File()],
) -> Document:
    await get_project_or_404(session, project_id)
    name = file.filename or "document"
    data = await file.read()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"{name} is larger than {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB",
        )

    try:
        text = extract_text(name, data)
    except UnsupportedDocumentError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except (EmptyDocumentError, DocumentError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    duplicate = await session.scalar(
        select(Document).where(
            Document.project_id == project_id, Document.fingerprint == fingerprint
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{name} is already indexed in this project (as {duplicate.name!r})",
        )

    try:
        document = await ingest_document(
            session, project_id=project_id, name=name, text=text, embedder=embedder
        )
    except EmbeddingError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc

    # get_session does not commit; without this the whole ingest is discarded.
    await session.commit()
    return document


@router.get("/{project_id}/documents", response_model=list[DocumentRead])
async def list_documents(project_id: UUID, session: SessionDep) -> list[Document]:
    await get_project_or_404(session, project_id)
    rows = await session.scalars(
        select(Document)
        .where(Document.project_id == project_id)
        .order_by(Document.created_at.desc())
    )
    return list(rows)


@documents.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: UUID, session: SessionDep) -> None:
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"Document {document_id} not found"
        )
    await session.delete(document)  # chunks cascade
    await session.commit()
