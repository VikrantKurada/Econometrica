"""What the upload endpoints put on the wire."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from econometrica.services.ingest import FileProfile, Role
from econometrica.services.mapping import ColumnMapping, MappingProposal


class ConfirmRequest(BaseModel):
    """The mapping a user has agreed to.

    Columns left out default to ``ignore`` — saying nothing about a column is
    not the same as asking for it, and making the client send every column would
    mean a forgotten one silently changed meaning.
    """

    roles: dict[str, Role]


class UploadRead(BaseModel):
    id: UUID
    project_id: UUID
    filename: str
    profile: FileProfile
    proposal: MappingProposal
    #: Whether a model decided the ambiguous columns. A billed turn, so it is
    #: reported rather than inferred.
    consulted_model: bool = False
    confirmed: bool = False
    mapping: ColumnMapping | None = None

    # Present only on the confirmation response: what that mapping would
    # actually ingest, which is what tells a user they mapped it right.
    observations: int | None = None
    symbols: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    #: The stored dataset a confirmation produced, which is what a run binds to.
    dataset_id: UUID | None = None


class DatasetRead(BaseModel):
    """A stored dataset, as the project's data list shows it."""

    id: UUID
    project_id: UUID
    name: str
    #: What a run built on this would report as its source.
    source_label: str
    rows: int
    column_roles: dict[str, str]
    fingerprint: str
    created_at: datetime
    symbols: list[str] = Field(default_factory=list)
