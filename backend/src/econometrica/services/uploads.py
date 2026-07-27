"""Where an uploaded file lives between arriving and being confirmed.

Profiling and confirming are two requests, and something has to hold the file
in between. §9 of the design also says the original blob is **retained**, so
this is not a temporary staging area that gets swept: a mapping revisited a
week later must not need the user to find the file again.

On disk under the storage directory, beside `keys.enc` and the price cache. The
record next to each blob carries the profile and the proposal — recomputing
them on every read would make an upload's description depend on when it was
looked at, and the profiler is deterministic precisely so it does not.

Task 6.8 adds the `datasets` table and the observations hypertable; the blob
and its record stay where they are, because a row in Postgres is not a file and
the design asks for both.
"""

import shutil
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from econometrica.services.ingest import FileProfile
from econometrica.services.mapping import ColumnMapping, MappingProposal


class UploadNotFoundError(LookupError):
    """No upload with that id has been stored."""


class StoredUpload(BaseModel):
    """One uploaded file: what it is, what it might mean, and what was agreed."""

    id: UUID
    project_id: UUID
    filename: str
    profile: FileProfile
    proposal: MappingProposal
    #: Whether a model was asked to decide the ambiguous columns. Recorded
    #: because it is a billed turn and the trace should be able to say so.
    consulted_model: bool = False
    #: Set only once a person has confirmed. Until then there is nothing here
    #: that anything downstream may act on.
    mapping: ColumnMapping | None = None

    @property
    def confirmed(self) -> bool:
        return self.mapping is not None and self.mapping.confirmed


class UploadStore:
    """Blobs and their records, addressed by upload id."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def blob_path(self, upload_id: UUID | str) -> Path:
        return self._directory(upload_id) / "original"

    def save(
        self,
        *,
        project_id: UUID,
        filename: str,
        source: Path,
        profile: FileProfile,
        proposal: MappingProposal,
        consulted_model: bool = False,
    ) -> StoredUpload:
        upload_id = uuid4()
        directory = self._directory(upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, self.blob_path(upload_id))

        record = StoredUpload(
            id=upload_id,
            project_id=project_id,
            filename=filename,
            profile=profile,
            proposal=proposal,
            consulted_model=consulted_model,
        )
        self._write(record)
        return record

    def get(self, upload_id: UUID | str) -> StoredUpload:
        path = self._directory(upload_id) / "record.json"
        if not path.is_file():
            raise UploadNotFoundError(f"no upload {upload_id}")
        return StoredUpload.model_validate_json(path.read_text(encoding="utf-8"))

    def confirm(self, upload_id: UUID | str, mapping: ColumnMapping) -> StoredUpload:
        record = self.get(upload_id)
        updated = record.model_copy(update={"mapping": mapping})
        self._write(updated)
        return updated

    # --- internals ----------------------------------------------------------

    def _directory(self, upload_id: UUID | str) -> Path:
        return self.root / str(upload_id)

    def _write(self, record: StoredUpload) -> None:
        path = self._directory(record.id) / "record.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2), encoding="utf-8")


class ConfirmationSummary(BaseModel):
    """What a confirmed mapping would actually ingest.

    Returned instead of a bare acknowledgement because "8 observations across
    AAPL and MSFT" is the sentence that tells a user they mapped it right, and
    a confirmation screen that says only "saved" makes them find out later.
    """

    observations: int
    symbols: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    start: str | None = None
    end: str | None = None
