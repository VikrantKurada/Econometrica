"""Uploading a file, and agreeing what its columns mean.

Three requests, and the middle one is the reason for the other two: post the
file and get back a profile plus a *suggested* mapping; look at it; confirm it.
§9 of the design puts a person between what a profiler inferred and what gets
stored, so no route here ingests anything a user has not agreed to.

Profiling happens on upload rather than on confirm because the confirmation
screen needs the candidates to offer, and because a file that cannot be read at
all should fail while the user is still looking at the file picker.

The model is optional throughout. A file whose every column has one obvious
role never reaches one, and a project with no `column_mapper` assigned falls
back to the profiler's proposal rather than refusing the upload — the user is
about to confirm it either way.
"""

import tempfile
from pathlib import Path
from typing import Annotated
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, File, HTTPException, UploadFile, status
from sqlalchemy import select

from econometrica.agents.column_mapper import ColumnMapper
from econometrica.api.deps import (
    ProviderRegistryDep,
    SessionDep,
    UploadStoreDep,
    get_project_or_404,
)
from econometrica.db.models import Dataset
from econometrica.schemas.upload import ConfirmRequest, DatasetRead, UploadRead
from econometrica.services.datasets import dataset_symbols, ingest_observations
from econometrica.services.ingest import IngestError, profile_upload
from econometrica.services.mapping import (
    MappingError,
    apply_mapping,
    confirm_mapping,
    propose_mapping,
)
from econometrica.services.uploads import (
    ConfirmationSummary,
    StoredUpload,
    UploadNotFoundError,
)

router = APIRouter(prefix="/api/projects", tags=["uploads"])
#: Reading and confirming need only the upload id; the project scoped the
#: creation and nothing after it has to repeat that.
uploads = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post(
    "/{project_id}/uploads", response_model=UploadRead, status_code=status.HTTP_201_CREATED
)
async def create_upload(
    project_id: UUID,
    session: SessionDep,
    registry: ProviderRegistryDep,
    store: UploadStoreDep,
    file: Annotated[UploadFile, File()],
) -> UploadRead:
    project = await get_project_or_404(session, project_id)
    filename = file.filename or "upload"

    # Written to disk first, because the size cap is checked against the file
    # rather than against a buffer already in memory.
    with tempfile.TemporaryDirectory() as staging:
        staged = Path(staging) / Path(filename).name
        staged.write_bytes(await file.read())

        try:
            profile = profile_upload(staged)
        except IngestError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc

        proposal = propose_mapping(profile)
        consulted = False
        assignment = (project.model_assignments or {}).get("column_mapper")
        if not proposal.unambiguous and assignment:
            provider = registry.build(assignment["provider"])
            proposal, result = await ColumnMapper(provider, assignment["model"]).propose(
                profile
            )
            consulted = result is not None

        record = store.save(
            project_id=project_id,
            filename=filename,
            source=staged,
            profile=profile,
            proposal=proposal,
            consulted_model=consulted,
        )

    return _read(record)


@uploads.get("/{upload_id}", response_model=UploadRead)
async def read_upload(upload_id: UUID, store: UploadStoreDep) -> UploadRead:
    return _read(_get(store, upload_id))


@router.get("/{project_id}/datasets", response_model=list[DatasetRead])
async def list_datasets(project_id: UUID, session: SessionDep) -> list[DatasetRead]:
    """Every upload of this project that has been confirmed and stored.

    An unconfirmed upload is deliberately absent: it has a blob and a proposal
    but no observations, and listing it as a dataset would offer a user data
    they have not agreed to.
    """
    await get_project_or_404(session, project_id)
    rows = await session.scalars(
        select(Dataset)
        .where(Dataset.project_id == project_id)
        .order_by(Dataset.created_at.desc())
    )
    return [
        DatasetRead(
            id=dataset.id,
            project_id=dataset.project_id,
            name=dataset.name,
            source_label=dataset.source_label,
            rows=dataset.rows,
            column_roles=dataset.column_roles,
            fingerprint=dataset.fingerprint,
            created_at=dataset.created_at,
            symbols=await dataset_symbols(session, dataset.id),
        )
        for dataset in rows.all()
    ]


@uploads.post("/{upload_id}/confirm", response_model=UploadRead)
async def confirm_upload(
    upload_id: UUID,
    payload: ConfirmRequest,
    session: SessionDep,
    store: UploadStoreDep,
) -> UploadRead:
    """Agree what the columns mean, and report what that would ingest.

    The summary is the point of returning anything at all: "8 observations
    across AAPL and MSFT" is the sentence that tells a user they mapped it
    right, where "saved" leaves them to find out later.
    """
    record = _get(store, upload_id)

    try:
        mapping = confirm_mapping(record.profile, payload.roles)
        observations = apply_mapping(
            _reread(store.blob_path(upload_id), record), mapping, record.profile
        )
    except MappingError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    dataset = await ingest_observations(
        session,
        project_id=record.project_id,
        upload_id=record.id,
        filename=record.filename,
        blob_path=str(store.blob_path(upload_id)),
        mapping=mapping,
        observations=observations,
    )
    # `get_session` does not commit, so without this the whole ingest — the
    # dataset row and every observation — is discarded when the request ends,
    # while the response still reports what it *would* have stored. The API
    # tests could not see it: `client` shares one session across a test, so the
    # flush stayed visible to the next request. The Phase 6 e2e found it, with
    # a 200 from here and an empty `GET /datasets` after it.
    await session.commit()

    return _read(store.confirm(upload_id, mapping), _summarise(observations), dataset.id)


# --- internals ---------------------------------------------------------------


def _get(store: UploadStoreDep, upload_id: UUID) -> StoredUpload:
    try:
        return store.get(upload_id)
    except UploadNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Upload {upload_id} not found"
        ) from exc


def _reread(path: Path, record: StoredUpload) -> pd.DataFrame:
    """The retained blob, read the same way the profiler read it.

    `dtype=str` for CSV so a decimal comma survives to `apply_mapping`, which
    knows from the profile how to parse it. Letting pandas guess here would turn
    `1,50` into text or into 150 depending on the column, and the profile's
    recorded convention would never get a chance to apply.
    """
    if record.profile.format == "csv":
        return pd.read_csv(
            path,
            sep=record.profile.delimiter or ",",
            engine="python",
            dtype=str,
            encoding="utf-8-sig",
        )
    if record.profile.format == "xlsx":
        return pd.read_excel(path)
    return pd.read_parquet(path)


def _summarise(observations: pd.DataFrame) -> ConfirmationSummary:
    if observations.empty:
        return ConfirmationSummary(observations=0)
    return ConfirmationSummary(
        observations=len(observations),
        symbols=sorted(observations["symbol"].unique().tolist()),
        fields=sorted(observations["field"].unique().tolist()),
        start=str(observations["ts"].min().date()),
        end=str(observations["ts"].max().date()),
    )


def _read(
    record: StoredUpload,
    summary: ConfirmationSummary | None = None,
    dataset_id: UUID | None = None,
) -> UploadRead:
    return UploadRead(
        id=record.id,
        project_id=record.project_id,
        filename=record.filename,
        profile=record.profile,
        proposal=record.proposal,
        consulted_model=record.consulted_model,
        confirmed=record.confirmed,
        mapping=record.mapping,
        observations=summary.observations if summary else None,
        symbols=summary.symbols if summary else [],
        fields=summary.fields if summary else [],
        dataset_id=dataset_id,
    )
