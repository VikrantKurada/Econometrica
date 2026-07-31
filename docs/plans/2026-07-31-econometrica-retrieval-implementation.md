# Document Retrieval — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user add documents to a project and have a run retrieve the relevant passages into the Planner's context — closing the retrieval half CLAUDE.md records as missing.

**Architecture:** A new db-free `tools/retrieval.py` holds the `Retriever` protocol and `RetrievalOutcome` so `agents/` never imports `db.models`; the concrete `ProjectRetriever` that touches the database lives in `services/rag.py`. A file-upload route extracts text (`.txt`/`.md` direct, `.pdf` via `pypdf`), chunks/embeds/stores it, and commits. A run builds a `ProjectRetriever` only when the project has indexed chunks (documents-presence is the gate — no toggle), feeds the results to the Planner only, and degrades on embedder failure like web search.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy + Alembic, Postgres/TimescaleDB + pgvector, `pypdf`, Ollama embeddings, pytest (async, `asyncio_mode=auto`), `uv`.

**Design note:** `docs/plans/2026-07-31-econometrica-retrieval-design.md`.

## Global Constraints

- **TDD, strictly.** Write the failing test, run it, watch it fail with the *expected* error, then implement.
- **`agents/` must never import `db.models`.** The `Retriever` protocol and `RetrievalOutcome` live in db-free `tools/retrieval.py`; the concrete `ProjectRetriever` lives in `services/rag.py`. The orchestrator imports only from `tools/retrieval.py`.
- **Retrieved text is never a source of numbers.** The grounding gate is untouched; retrieved passages go to the **Planner only, never the Narrator**. The existing `test_a_number_read_from_a_document_is_still_ungrounded` stays green.
- Backend commands run under `uv` from `backend/`: `uv run pytest -q`, `uv run ruff check src tests alembic`, `uv run mypy src`.
- **DB tests need Postgres:** `docker compose up -d db --wait`.
- **`mypy --strict` clean on `src`; ruff clean** on `src tests alembic`. No new violations.
- **Commit with `git commit -F <file>`** from the Bash tool (heredoc). End every body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Branch:** `feat/foundation`.
- Retrieval constants: `DEFAULT_LIMIT = 5` (in `rag.py`), `MAX_DOCUMENT_BYTES = 8 * 1024 * 1024`.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/econometrica/tools/retrieval.py` | New, db-free. `Retrieved`, `RetrievalOutcome` (`to_step_record`/`as_context`), `Retriever` protocol. | 1 |
| `src/econometrica/services/rag.py` | Modify. Import `Retrieved`/`RetrievalOutcome` from `tools/retrieval`; drop local copies. | 1 |
| `tests/tools/test_retrieval.py` | New. `RetrievalOutcome` unit tests. | 1 |
| `src/econometrica/services/documents.py` | New. `extract_text` + error types. | 2 |
| `tests/services/test_documents.py` | New. Extraction tests. | 2 |
| `pyproject.toml` | Modify. Add `pypdf`. | 2 |
| `src/econometrica/schemas/document.py` | New. `DocumentRead`. | 3 |
| `src/econometrica/api/deps.py` | Modify. `get_embedder` / `EmbedderDep`. | 3 |
| `src/econometrica/api/routers/documents.py` | New. POST / GET / DELETE. | 3 |
| `src/econometrica/main.py` | Modify. Register the routers. | 3 |
| `tests/api/test_documents.py` | New. Route tests. | 3 |
| `src/econometrica/services/rag.py` | Modify. `ProjectRetriever`. | 4 |
| `tests/services/test_rag.py` | Modify. `ProjectRetriever` tests. | 4 |
| `src/econometrica/agents/orchestrator.py` | Modify. `retriever` param + `_retrieval_context`. | 5 |
| `tests/agents/test_orchestrator.py` | Modify. `SpyRetriever` + tests. | 5 |
| `src/econometrica/api/routers/runs.py` | Modify. Build `ProjectRetriever` when chunks exist. | 6 |
| `tests/api/test_runs.py` | Modify. Retrieval-in-a-run tests. | 6 |
| `CLAUDE.md`, `README.md` | Modify. Record retrieval wired end to end. | 7 |

---

## Task 1: The db-free seam — `tools/retrieval.py`

**Files:**
- Create: `src/econometrica/tools/retrieval.py`
- Modify: `src/econometrica/services/rag.py` (imports; remove local `Retrieved` and `as_context`)
- Test: `tests/tools/test_retrieval.py`

**Interfaces:**
- Consumes: `econometrica.agents.trace.StepRecord`.
- Produces:
  - `Retrieved(document_id: UUID, document_name: str, ordinal: int, text: str, score: float)` — frozen dataclass.
  - `RetrievalOutcome(model: str, query: str, hits: list[Retrieved] = [], failed: bool = False, detail: str = "")` with `as_context() -> str` and `to_step_record() -> StepRecord`.
  - `Retriever` — `Protocol` with `model: str` and `async def fetch(self, query: str) -> RetrievalOutcome`.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_retrieval.py`:

```python
"""RetrievalOutcome: the shape the trace and the prompt both want, db-free."""

from uuid import uuid4

from econometrica.tools.retrieval import Retrieved, RetrievalOutcome


def a_hit(name="notes.txt", ordinal=0, text="Beta exceeded one.") -> Retrieved:
    return Retrieved(
        document_id=uuid4(), document_name=name, ordinal=ordinal, text=text, score=0.9
    )


def test_the_step_record_names_the_model_and_query_under_the_planner():
    outcome = RetrievalOutcome(model="all-minilm", query="beta", hits=[a_hit()])

    step = outcome.to_step_record()

    assert step.agent == "planner"  # the retrieval feeds the planner
    assert step.kind == "tool"
    assert step.tool == "retrieval:all-minilm"
    assert step.status == "ok"
    assert "beta" in step.detail
    assert "1 passage" in step.detail


def test_a_failed_outcome_is_a_failed_step_with_its_reason():
    outcome = RetrievalOutcome(
        model="all-minilm", query="beta", failed=True, detail="ollama unreachable"
    )

    step = outcome.to_step_record()

    assert step.status == "failed"
    assert "ollama unreachable" in step.detail


def test_the_context_attributes_every_passage_and_marks_it_read_not_computed():
    outcome = RetrievalOutcome(
        model="all-minilm",
        query="beta",
        hits=[a_hit(name="a.txt", ordinal=2, text="Beta exceeded one.")],
    )

    context = outcome.as_context()

    assert "a.txt" in context and "#2" in context
    assert "Beta exceeded one." in context
    assert "not computed" in context.lower()


def test_an_empty_outcome_produces_no_context():
    assert RetrievalOutcome(model="m", query="q").as_context() == ""
    assert RetrievalOutcome(model="m", query="q", failed=True, detail="x").as_context() == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/tools/test_retrieval.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'econometrica.tools.retrieval'`.

- [ ] **Step 3: Write `tools/retrieval.py`**

Create `src/econometrica/tools/retrieval.py`:

```python
"""Retrieved passages, in the shape the trace and the prompt both want.

Deliberately free of `db.models`. The orchestrator has to call retrieval before
planning and record it as a trace step, but `agents/` must never import the
database layer — the same rule that makes `search()` take a bare `enabled: bool`.
So the protocol the orchestrator sees, and the outcome it records, live here; the
concrete `ProjectRetriever` that touches the database lives in `services/rag.py`,
behind this protocol.

The mirror of `tools/web_search.SearchOutcome`, and for the same reasons: every
retrieval is an attributed trace step, and nothing it returns is a result — the
grounding gate admits only what a tool computed.
"""

from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from econometrica.agents.trace import StepRecord

#: Kept on the trace step. The query and model are the audit; the passages
#: themselves are in the prompt where they were used.
_DETAIL_LIMIT = 300


@dataclass(frozen=True)
class Retrieved:
    """One passage, and where it came from."""

    document_id: UUID
    document_name: str
    ordinal: int
    text: str
    #: 0 to 1, from cosine distance. Comparable within one query only.
    score: float


@dataclass(frozen=True)
class RetrievalOutcome:
    """One retrieval, for the trace and the prompt."""

    model: str
    query: str
    hits: list[Retrieved] = field(default_factory=list)
    failed: bool = False
    detail: str = ""

    def as_context(self) -> str:
        """The passages as prompt text, each attributed.

        The header marks the text as *read* rather than *computed*, the same
        wording web search uses. It is for the model; the grounding gate is what
        actually enforces it.
        """
        if not self.hits:
            return ""
        blocks = [f"[{hit.document_name} #{hit.ordinal}]\n{hit.text}" for hit in self.hits]
        return (
            "# Retrieved context — read from documents, not computed.\n"
            "Nothing here is a result. Do not cite a number from it.\n\n"
            + "\n\n".join(blocks)
        )

    def to_step_record(self) -> StepRecord:
        return StepRecord(
            agent="planner",
            kind="tool",
            status="failed" if self.failed else "ok",
            tool=f"retrieval:{self.model}",
            detail=(
                f"{self.query} — {self.detail}"
                if self.failed
                else f"{self.query} — {len(self.hits)} passage(s)"
            )[:_DETAIL_LIMIT],
        )


class Retriever(Protocol):
    """What the orchestrator sees. The concrete implementation holds the session
    and project; this hides both, so `agents/` stays free of `db.models`."""

    model: str

    async def fetch(self, query: str) -> RetrievalOutcome:
        ...
```

- [ ] **Step 4: Move `Retrieved`/`as_context` out of `rag.py`**

In `src/econometrica/services/rag.py`:

- Delete the local `Retrieved` dataclass (lines ~77-86) and the `as_context` function (lines ~250-267).
- Add to the imports: `from econometrica.tools.retrieval import Retrieved, RetrievalOutcome`.
  (`RetrievalOutcome` is imported now for use in Task 4; unused imports fail ruff, so if Task 1 is committed before Task 4, import only `Retrieved` here and add `RetrievalOutcome` in Task 4. Import `Retrieved` now.)

`retrieve()` still returns `list[Retrieved]` — the type now comes from `tools/retrieval`. Nothing else in `rag.py` changes.

- [ ] **Step 5: Run the tests and confirm nothing else broke**

Run:
```bash
uv run pytest tests/tools/test_retrieval.py tests/services/test_rag.py -q
```
Expected: PASS. `test_rag.py` imports neither `Retrieved` nor `as_context` by name and uses hits structurally, so it is unaffected by the move.

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src tests/tools tests/services/test_rag.py` and `uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/r1.txt <<'EOF'
refactor(retrieval): a db-free seam for retrieval outcomes

The orchestrator has to call retrieval before planning and trace it, but
agents/ must never import db.models -- the rule that makes search() take a
bare enabled bool. So Retrieved and a new RetrievalOutcome (to_step_record
+ as_context, mirroring SearchOutcome) move into a db-free tools/retrieval,
behind a Retriever protocol. services/rag imports them back; its retrieve()
is unchanged. The concrete retriever that touches the database follows in a
later task and lives in services/, behind this protocol.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/tools/retrieval.py backend/src/econometrica/services/rag.py backend/tests/tools/test_retrieval.py
git commit -F /tmp/r1.txt && rm -f /tmp/r1.txt
```

---

## Task 2: Text extraction — `services/documents.py`

**Files:**
- Modify: `pyproject.toml` (add `pypdf`)
- Create: `src/econometrica/services/documents.py`
- Test: `tests/services/test_documents.py`

**Interfaces:**
- Produces:
  - `class DocumentError(ValueError)`, `class UnsupportedDocumentError(DocumentError)`, `class EmptyDocumentError(DocumentError)`.
  - `extract_text(filename: str, data: bytes) -> str`.
  - `SUPPORTED_SUFFIXES: frozenset[str]`.

- [ ] **Step 1: Add the dependency**

Run: `uv add "pypdf>=5.1"`
This updates `pyproject.toml` and `uv.lock`.

- [ ] **Step 2: Write the failing tests**

Create `tests/services/test_documents.py`:

```python
"""Turning an uploaded file into the text retrieval indexes."""

import io

import pytest

from econometrica.services.documents import (
    EmptyDocumentError,
    UnsupportedDocumentError,
    extract_text,
)


def test_a_text_file_is_its_own_content():
    assert extract_text("notes.txt", b"Beta exceeded one.") == "Beta exceeded one."


def test_a_markdown_file_is_read_as_text():
    assert "heading" in extract_text("r.md", b"# heading\n\nbody").lower()


def test_a_pdf_with_text_yields_that_text():
    """A PDF carrying real text extracts it. Read from a tiny committed fixture,
    because laying out text into a PDF in-process needs a layout library
    (reportlab) that is not — and should not become — a dependency."""
    from pathlib import Path

    pdf = Path(__file__).parent / "fixtures" / "one_line.pdf"
    text = extract_text("one_line.pdf", pdf.read_bytes())

    assert "volatility" in text.lower()  # the fixture's single line


def test_a_blank_pdf_is_refused_as_empty():
    """A blank or image-only (scanned) PDF extracts to nothing, so it is refused
    the same way an empty text file is — retrieval never indexes nothing."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(EmptyDocumentError):
        extract_text("blank.pdf", buffer.getvalue())


def test_an_unknown_type_is_refused_naming_the_supported_set():
    with pytest.raises(UnsupportedDocumentError, match=r"\.txt"):
        extract_text("data.xlsx", b"...")


def test_a_document_that_is_all_whitespace_is_refused():
    with pytest.raises(EmptyDocumentError):
        extract_text("blank.txt", b"   \n\t ")
```

**The PDF fixture is a prerequisite for this task.** Before running the tests,
create `tests/services/fixtures/one_line.pdf` containing a single known line —
`Volatility clustering is a stylized fact.` — with any PDF tool (print-to-PDF, a
one-off `reportlab` script run outside the project, etc.). The fixture is
committed with Task 2. `test_a_blank_pdf_is_refused_as_empty` needs no fixture
and guards the scanned/empty-PDF path on its own.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/services/test_documents.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'econometrica.services.documents'`.

- [ ] **Step 4: Implement `services/documents.py`**

```python
"""Turning an uploaded file into the text retrieval indexes.

Kept separate from the route and from `rag.py`: the route handles HTTP, `rag.py`
chunks and embeds, and this decides only how bytes become text. Text formats are
read directly; PDFs go through `pypdf`. Anything else is refused by name rather
than mis-parsed — a `.docx` read as UTF-8 is mojibake that would embed as noise.
"""

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfError

#: Read directly as UTF-8. Everything else is refused.
_TEXT_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".text"})
SUPPORTED_SUFFIXES = _TEXT_SUFFIXES | {".pdf"}


class DocumentError(ValueError):
    """A file could not be turned into indexable text."""


class UnsupportedDocumentError(DocumentError):
    """The file type is not one this system extracts text from."""


class EmptyDocumentError(DocumentError):
    """The file parsed but carried no text to index."""


def extract_text(filename: str, data: bytes) -> str:
    """The document's text, or a `DocumentError` naming why not."""
    suffix = Path(filename).suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        text = data.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        text = _extract_pdf(data)
    else:
        raise UnsupportedDocumentError(
            f"cannot index {suffix or 'a file with no extension'};"
            f" supported types are {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    if not text.strip():
        # A scanned (image-only) PDF lands here, and so does an empty text file.
        raise EmptyDocumentError(f"{filename} carried no text to index")
    return text


def _extract_pdf(data: bytes) -> str:
    import io

    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfError, ValueError) as exc:
        raise DocumentError(f"the PDF could not be read: {exc}") from exc
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/services/test_documents.py -q`
Expected: PASS.

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src tests/services/test_documents.py` and `uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/r2.txt <<'EOF'
feat(documents): extract text from uploaded files for retrieval

A new services/documents.extract_text turns an uploaded file into
indexable text: .txt/.md read as UTF-8, .pdf through pypdf, anything else
refused by name rather than mis-parsed as mojibake. A parsed-but-empty
file (an image-only scanned PDF, or a blank text file) is refused too, so
retrieval never indexes nothing. pypdf is a new dependency.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/pyproject.toml backend/uv.lock backend/src/econometrica/services/documents.py backend/tests/services/test_documents.py backend/tests/services/fixtures/one_line.pdf
git commit -F /tmp/r2.txt && rm -f /tmp/r2.txt
```

---

## Task 3: The documents routes

**Files:**
- Create: `src/econometrica/schemas/document.py`
- Modify: `src/econometrica/api/deps.py` (`get_embedder`, `EmbedderDep`)
- Create: `src/econometrica/api/routers/documents.py`
- Modify: `src/econometrica/main.py` (register both routers)
- Test: `tests/api/test_documents.py`

**Interfaces:**
- Consumes: `extract_text` + error types (Task 2); `rag.ingest_document`, `rag.EmbeddingError`; `Document` model.
- Produces: `POST /api/projects/{id}/documents`, `GET /api/projects/{id}/documents`, `DELETE /api/documents/{id}`; `DocumentRead`; `get_embedder`/`EmbedderDep`.

- [ ] **Step 1: Write the failing route tests**

Create `tests/api/test_documents.py`:

```python
"""Adding, listing and removing a project's documents."""

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from econometrica.api.deps import get_embedder
from econometrica.db.models import DocumentChunk
from econometrica.main import app


class FakeEmbedder:
    model = "fake-embed"
    dimensions = 8

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.error is not None:
            raise self.error
        return [[float(len(t) % 7)] + [0.0] * 7 for t in texts]


@pytest_asyncio.fixture
async def embedding():
    fake = FakeEmbedder()
    app.dependency_overrides[get_embedder] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_embedder, None)


async def a_project(client, name="Docs") -> str:
    return (await client.post("/api/projects", json={"name": name})).json()["id"]


def a_file(name="notes.txt", body=b"Beta exceeded one. Volatility clustered."):
    return {"file": (name, body, "text/plain")}


async def test_a_document_is_added_and_then_listed(client, embedding):
    project = await a_project(client)

    created = await client.post(f"/api/projects/{project}/documents", files=a_file())
    assert created.status_code == 201
    assert created.json()["chunks_count"] >= 1

    listed = await client.get(f"/api/projects/{project}/documents")
    assert [d["name"] for d in listed.json()] == ["notes.txt"]


async def test_adding_a_document_commits_so_it_survives_the_request(client, session, embedding):
    """A flush is not a write, and the shared-session fixture cannot tell them
    apart — a nested commit is durable to no separate session either. Counting
    the commits is the only signal, as the uploads confirm-commit test does."""
    project = await a_project(client)

    commits = 0
    original = session.commit

    async def counting_commit():
        nonlocal commits
        commits += 1
        await original()

    session.commit = counting_commit  # type: ignore[method-assign]
    try:
        response = await client.post(f"/api/projects/{project}/documents", files=a_file())
    finally:
        session.commit = original  # type: ignore[method-assign]

    assert response.status_code == 201
    assert commits >= 1, "adding a document must commit; a flush is rolled back"


async def test_the_same_document_twice_is_a_conflict(client, embedding):
    project = await a_project(client)
    await client.post(f"/api/projects/{project}/documents", files=a_file())

    again = await client.post(f"/api/projects/{project}/documents", files=a_file())

    assert again.status_code == 409


async def test_an_unsupported_type_is_415(client, embedding):
    project = await a_project(client)

    response = await client.post(
        f"/api/projects/{project}/documents",
        files={"file": ("data.xlsx", b"...", "application/octet-stream")},
    )

    assert response.status_code == 415


async def test_a_document_with_no_text_is_400(client, embedding):
    project = await a_project(client)

    response = await client.post(
        f"/api/projects/{project}/documents", files=a_file(body=b"   \n  ")
    )

    assert response.status_code == 400


async def test_an_embedder_that_is_down_is_502_and_stores_nothing(client, session):
    fake = FakeEmbedder(error=RuntimeError("model not pulled"))
    app.dependency_overrides[get_embedder] = lambda: fake
    try:
        project = await a_project(client)
        response = await client.post(f"/api/projects/{project}/documents", files=a_file())
    finally:
        app.dependency_overrides.pop(get_embedder, None)

    assert response.status_code == 502
    count = await session.scalar(select(func.count()).select_from(DocumentChunk))
    assert count == 0


async def test_deleting_a_document_removes_its_chunks(client, session, embedding):
    project = await a_project(client)
    doc_id = (await client.post(f"/api/projects/{project}/documents", files=a_file())).json()["id"]

    response = await client.delete(f"/api/documents/{doc_id}")

    assert response.status_code == 204
    count = await session.scalar(select(func.count()).select_from(DocumentChunk))
    assert count == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_documents.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_embedder'` (and the routes 404 once that is fixed).

- [ ] **Step 3: Add the embedder dependency**

In `src/econometrica/api/deps.py`, add near the other builders:

```python
from econometrica.llm.embeddings import OllamaEmbedder
from econometrica.services.rag import Embedder


def get_embedder() -> Embedder:
    """The embedding model for documents and retrieval.

    `all-minilm` at 384 dimensions, the width `document_chunks.embedding` is
    declared at. Constructing it is cheap and touches no network — `.embed` is
    the only call that reaches Ollama — so a run that ends up not retrieving
    pays nothing for holding one.
    """
    return OllamaEmbedder()


EmbedderDep = Annotated[Embedder, Depends(get_embedder)]
```

- [ ] **Step 4: Write the schema**

Create `src/econometrica/schemas/document.py`:

```python
"""What the document endpoints put on the wire."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    chars: int
    chunks_count: int
    created_at: datetime
```

- [ ] **Step 5: Write the router**

Create `src/econometrica/api/routers/documents.py`:

```python
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
```

- [ ] **Step 6: Register the routers**

In `src/econometrica/main.py`, add `documents` to the router import tuple (alphabetical: after `chats`), and register both:

```python
app.include_router(documents.router)
app.include_router(documents.documents)
```

- [ ] **Step 7: Run the route tests**

Run: `docker compose up -d db --wait` then `uv run pytest tests/api/test_documents.py -q`
Expected: PASS (7 tests).

- [ ] **Step 8: Lint and type-check**

Run: `uv run ruff check src tests/api/test_documents.py` and `uv run mypy src`
Expected: clean.

- [ ] **Step 9: Commit**

```bash
cat > /tmp/r3.txt <<'EOF'
feat(documents): routes to add, list and remove a project's documents

POST a file (multipart) -> extract text, dedupe by fingerprint (409),
chunk/embed/store, and commit -- the upload-never-committed scar, so its
test counts commits. Embedder down is 502: a document cannot be stored
without vectors and has nothing to fall back to. GET lists what a project
has indexed; DELETE removes a document and cascades its chunks. A new
get_embedder dependency constructs the Ollama embedder, cheap and
network-free until embed() is called.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/schemas/document.py backend/src/econometrica/api/deps.py \
  backend/src/econometrica/api/routers/documents.py backend/src/econometrica/main.py \
  backend/tests/api/test_documents.py
git commit -F /tmp/r3.txt && rm -f /tmp/r3.txt
```

---

## Task 4: `ProjectRetriever` — the concrete retriever

**Files:**
- Modify: `src/econometrica/services/rag.py` (add `ProjectRetriever`; import `RetrievalOutcome`)
- Test: `tests/services/test_rag.py` (add a `ProjectRetriever` section)

**Interfaces:**
- Consumes: `RetrievalOutcome` (Task 1); `retrieve`, `EmbeddingError`, `Embedder`, `DEFAULT_LIMIT` (existing).
- Produces: `ProjectRetriever(session, project_id, embedder, *, limit=DEFAULT_LIMIT)` with `model: str` and `async def fetch(self, query: str) -> RetrievalOutcome`, satisfying the `Retriever` protocol.

- [ ] **Step 1: Write the failing tests**

Add to `tests/services/test_rag.py` (imports `ProjectRetriever` from `rag`, and reuses the file's `FakeEmbedder`/`make_project`):

```python
# --- the concrete retriever -----------------------------------------------------


async def test_project_retriever_returns_an_attributed_outcome(session):
    from econometrica.services.rag import ProjectRetriever

    project = await make_project(session)
    embedder = FakeEmbedder()
    await ingest_document(
        session, project_id=project.id, name="notes.txt",
        text="Beta exceeded one.", embedder=embedder,
    )

    outcome = await ProjectRetriever(session, project.id, embedder).fetch("beta")

    assert outcome.failed is False
    assert outcome.model == "fake-embed"
    assert outcome.query == "beta"
    assert outcome.hits and outcome.hits[0].document_name == "notes.txt"


async def test_project_retriever_degrades_when_the_embedder_fails(session):
    from econometrica.services.rag import ProjectRetriever

    project = await make_project(session)
    broken = FakeEmbedder(error=RuntimeError("model not pulled"))

    outcome = await ProjectRetriever(session, project.id, broken).fetch("beta")

    # Degrades, does not raise: a run with less context beats a lost run.
    assert outcome.failed is True
    assert "not pulled" in outcome.detail
    assert outcome.hits == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/services/test_rag.py -k project_retriever -q`
Expected: FAIL — `ImportError: cannot import name 'ProjectRetriever'`.

- [ ] **Step 3: Implement `ProjectRetriever`**

In `src/econometrica/services/rag.py`, ensure the import line reads
`from econometrica.tools.retrieval import Retrieved, RetrievalOutcome`, then add
at the end of the module:

```python
class ProjectRetriever:
    """The concrete `Retriever`, bound to one project's documents.

    Holds the session and project so the orchestrator does not have to — it sees
    only `tools.retrieval.Retriever`. An embedding failure mid-run becomes a
    *failed* outcome rather than a raise: retrieval is context, and a run with
    less context is better than a run lost to an unreachable model.
    """

    def __init__(
        self,
        session: AsyncSession,
        project_id: UUID,
        embedder: Embedder,
        *,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._embedder = embedder
        self._limit = limit
        self.model = embedder.model

    async def fetch(self, query: str) -> RetrievalOutcome:
        try:
            hits = await retrieve(
                self._session,
                project_id=self._project_id,
                query=query,
                embedder=self._embedder,
                limit=self._limit,
            )
        except EmbeddingError as exc:
            return RetrievalOutcome(model=self.model, query=query, failed=True, detail=str(exc))
        return RetrievalOutcome(model=self.model, query=query, hits=hits)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/services/test_rag.py -q`
Expected: PASS (the new two plus the existing suite).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src tests/services/test_rag.py` and `uv run mypy src`
Expected: clean. `ProjectRetriever` structurally satisfies `tools.retrieval.Retriever`; no explicit inheritance is needed.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/r4.txt <<'EOF'
feat(retrieval): a project-scoped concrete retriever

ProjectRetriever binds a session, a project and an embedder and implements
the db-free Retriever protocol, so the orchestrator sees only the protocol.
An embedding failure mid-run becomes a failed RetrievalOutcome rather than
a raise: retrieval is context, and a run with less context beats a run lost
to an unreachable model. Ingest still fails loudly -- the asymmetry is that
ingest has nothing to fall back to and a run does.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/services/rag.py backend/tests/services/test_rag.py
git commit -F /tmp/r4.txt && rm -f /tmp/r4.txt
```

---

## Task 5: Run wiring — the orchestrator retrieves

**Files:**
- Modify: `src/econometrica/agents/orchestrator.py` (import `Retriever`, `retriever` param, `_retrieval_context`, call in `_pipeline`)
- Test: `tests/agents/test_orchestrator.py` (`SpyRetriever` + tests)

**Interfaces:**
- Consumes: `Retriever`, `RetrievalOutcome` (Task 1).
- Produces: `Orchestrator.__init__(..., retriever: Retriever | None = None)`; a `_retrieval_context` that appends retrieved passages to the Planner's context and records a `retrieval:<model>` step, run before `_search_context`.

- [ ] **Step 1: Write the failing tests**

In `tests/agents/test_orchestrator.py`, add near the web-search helpers:

```python
class SpyRetriever:
    """A retriever that returns a scripted outcome and records the query."""

    def __init__(self, *, hits=None, fail: str = "") -> None:
        from econometrica.tools.retrieval import Retrieved

        self.model = "spy-embed"
        self._hits = [] if hits is None else hits
        self._fail = fail
        self.asked: list[str] = []
        self._Retrieved = Retrieved

    async def fetch(self, query: str):
        from econometrica.tools.retrieval import RetrievalOutcome

        self.asked.append(query)
        if self._fail:
            return RetrievalOutcome(model=self.model, query=query, failed=True, detail=self._fail)
        return RetrievalOutcome(model=self.model, query=query, hits=self._hits)


def a_passage():
    from econometrica.tools.retrieval import Retrieved
    from uuid import uuid4

    return Retrieved(
        document_id=uuid4(),
        document_name="methodology.md",
        ordinal=0,
        text="Use the Fama-French five-factor model for this asset class.",
        score=0.9,
    )


def retrieval_step(outcome):
    return next(s for s in outcome.trace if (s.tool or "").startswith("retrieval:"))
```

Extend `build()` to accept `retriever` and pass it through:

```python
    retriever: object | None = None,
```
and in the `Orchestrator(...)` call add `retriever=retriever,`.

Then the tests:

```python
async def test_retrieved_passages_reach_the_planner():
    spy = SpyRetriever(hits=[a_passage()])
    orchestrator, fakes = build(retriever=spy)

    await orchestrator.run(QUESTION)

    assert spy.asked == [QUESTION]
    assert "read from documents, not computed" in planner_prompt(fakes)
    assert "Fama-French five-factor" in planner_prompt(fakes)


async def test_without_a_retriever_nothing_is_retrieved():
    orchestrator, _ = build()  # retriever is None

    outcome = await orchestrator.run(QUESTION)

    assert not any((s.tool or "").startswith("retrieval:") for s in outcome.trace)


async def test_a_failed_retrieval_degrades_the_run():
    spy = SpyRetriever(fail="ollama unreachable")
    orchestrator, fakes = build(retriever=spy)

    outcome = await orchestrator.run(QUESTION)

    assert outcome.status == "completed"
    assert "read from documents" not in planner_prompt(fakes)
    assert retrieval_step(outcome).status == "failed"


async def test_the_retrieval_step_precedes_the_plan():
    spy = SpyRetriever(hits=[a_passage()])
    orchestrator, _ = build(retriever=spy)

    outcome = await orchestrator.run(QUESTION)

    retrieval = outcome.trace.index(retrieval_step(outcome))
    plan = next(
        i for i, s in enumerate(outcome.trace) if s.agent == "planner" and s.kind == "llm"
    )
    assert retrieval < plan
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/agents/test_orchestrator.py -k "retriev or passage" -q`
Expected: FAIL — `Orchestrator.__init__() got an unexpected keyword argument 'retriever'`.

- [ ] **Step 3: Add the import and constructor parameter**

In `src/econometrica/agents/orchestrator.py`, add to the imports:

```python
from econometrica.tools.retrieval import Retriever
```

Add the parameter to `__init__` (after `query_writer: QueryWriter | None = None,`):

```python
        retriever: Retriever | None = None,
```

and store it (after `self.query_writer = query_writer`):

```python
        #: A project's own documents, retrieved for the Planner. Passed in like
        #: `searcher`: `agents/` knows nothing about projects, and the router
        #: supplies one only when the project has documents to retrieve.
        self.retriever = retriever
```

- [ ] **Step 4: Add `_retrieval_context` and call it in `_pipeline`**

In `_pipeline`, change the search line to run retrieval first:

```python
        context = await self._retrieval_context(question, context, trace)
        context = await self._search_context(question, context, trace)
```

Add the method beside `_search_context`:

```python
    async def _retrieval_context(
        self, question: str, context: str, trace: TraceBuilder
    ) -> str:
        """A project's own documents as extra context for the Planner.

        The Planner benefits and the Narrator must not: the grounding gate judges
        the narration and withholds a whole one over a single number it cannot
        match, and retrieved passages are dense with numbers — the same reason
        web search is withheld from it. A retrieval that fails (the embedder is
        unreachable) degrades the run rather than failing it.
        """
        if self.retriever is None:
            return context

        outcome = await self.retriever.fetch(question)
        record = outcome.to_step_record()
        record.prompt = question[:PROMPT_LIMIT]
        record.response = outcome.as_context()[:PROMPT_LIMIT]
        trace.add(record)

        found = outcome.as_context()
        if not found:
            return context
        return f"{context}\n\n{found}" if context else found
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/agents/test_orchestrator.py -q`
Expected: PASS (the whole file).

- [ ] **Step 6: Lint and type-check**

Run: `uv run ruff check src tests/agents/test_orchestrator.py` and `uv run mypy src`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
cat > /tmp/r5.txt <<'EOF'
feat(runs): a run retrieves its project's documents before planning

The orchestrator gains a retriever, passed in like searcher, and a
_retrieval_context that runs before web search: it fetches the project's
relevant passages, records a retrieval:<model> step under agent="planner"
(the retrieval feeds the planner, so no new trace vocabulary), and appends
them to the Planner's context. Never the Narrator -- same grounding-gate
reason web search is withheld. A failed retrieval degrades the run.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/agents/orchestrator.py backend/tests/agents/test_orchestrator.py
git commit -F /tmp/r5.txt && rm -f /tmp/r5.txt
```

---

## Task 6: Router wiring — build a retriever when the project has documents

**Files:**
- Modify: `src/econometrica/api/routers/runs.py` (`start_run` gains `EmbedderDep`; `_build` counts chunks and builds a `ProjectRetriever`)
- Test: `tests/api/test_runs.py`

**Interfaces:**
- Consumes: `ProjectRetriever` (Task 4); `EmbedderDep` (Task 3); `Orchestrator(retriever=...)` (Task 5); `DocumentChunk` model.
- Produces: a run whose orchestrator retrieves exactly when the project has at least one indexed chunk.

- [ ] **Step 1: Write the failing tests**

Add to the web-search section of `tests/api/test_runs.py` (reusing `events`, `scripted`, `QUESTION`, `NARRATIVE`, `PLAN`):

```python
class RunFakeEmbedder:
    model = "fake-embed"
    dimensions = 8

    async def embed(self, texts):
        return [[float(len(t) % 5)] + [0.0] * 7 for t in texts]


async def test_a_run_retrieves_when_the_project_has_documents(client, scripted, session):
    from econometrica.api.deps import get_embedder
    from econometrica.services.rag import ingest_document

    fake = RunFakeEmbedder()
    app.dependency_overrides[get_embedder] = lambda: fake
    scripted.provider.responses = [json.dumps(PLAN), NARRATIVE]
    try:
        project = (await client.post("/api/projects", json={"name": "R"})).json()
        await client.patch(
            f"/api/projects/{project['id']}",
            json={
                "validation_tier": "single",
                "model_assignments": {
                    "planner": {"provider": "ollama", "model": "fake-1"},
                    "narrator": {"provider": "ollama", "model": "fake-1"},
                },
            },
        )
        # A document to retrieve, ingested through the same shared session.
        from uuid import UUID

        await ingest_document(
            session, project_id=UUID(project["id"]), name="m.txt",
            text="Beta exceeded one.", embedder=fake,
        )
        await session.flush()

        chat = (
            await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
        ).json()
        response = await client.post(
            f"/api/chats/{chat['id']}/runs", json={"question": "beta"}
        )
    finally:
        app.dependency_overrides.pop(get_embedder, None)

    assert response.status_code == 200
    trace = events(response.text)[-1]["data"]["payload"]["trace"]
    assert any((s["tool"] or "").startswith("retrieval:") for s in trace)


async def test_a_run_without_documents_does_not_retrieve(client, scripted):
    chat_id = await make_chat(client)  # a fresh project, no documents

    response = await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    trace = events(response.text)[-1]["data"]["payload"]["trace"]
    assert not any((s["tool"] or "").startswith("retrieval:") for s in trace)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_runs.py -k "retriev" -q`
Expected: FAIL — the retrieval step is absent because `_build` constructs no retriever.

- [ ] **Step 3: Wire `_build`**

In `src/econometrica/api/routers/runs.py`:

Add imports:
```python
from econometrica.api.deps import EmbedderDep  # add to the existing deps import
from econometrica.db.models import Chat, DocumentChunk, Project  # add DocumentChunk
from econometrica.services.rag import ProjectRetriever
```

Give `start_run` the embedder and pass it to `_build`:
```python
async def start_run(
    chat_id: UUID,
    payload: RunStart,
    session: SessionDep,
    registry: ProviderRegistryDep,
    source: PriceSourceDep,
    rate_source: RateSourceDep,
    factor_source: FactorSourceDep,
    embedder: EmbedderDep,
) -> EventSourceResponse:
    ...
    orchestrator = await _build(
        project, chat, registry, source, rate_source, factor_source, session, embedder
    )
```

Add `embedder` to `_build`'s signature and build the retriever (after the `query_writer` block, before `coder`):

```python
    # Documents-presence is the gate: no toggle. A run retrieves whenever the
    # project has indexed chunks, and never embeds when it has none. The check is
    # an EXISTS, not a COUNT — one row is enough to know.
    retriever = None
    has_documents = await session.scalar(
        select(DocumentChunk.document_id)
        .where(DocumentChunk.project_id == project.id)
        .limit(1)
    )
    if has_documents is not None:
        retriever = ProjectRetriever(session, project.id, embedder)
```

Pass it to the orchestrator:
```python
        query_writer=query_writer,
        retriever=retriever,
        tier=tier,
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_runs.py -q`
Expected: PASS — the whole runs suite, including the two new tests and the unchanged ones (a project without documents builds no retriever, so `get_embedder`'s default `OllamaEmbedder` is constructed but never reached).

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src tests/api/test_runs.py` and `uv run mypy src`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cat > /tmp/r6.txt <<'EOF'
feat(runs): build a retriever when the project has documents

_build runs an EXISTS on the project's chunks and constructs a
ProjectRetriever only when there is one -- documents-presence is the gate,
no toggle, no migration. A project that never used retrieval runs exactly
as before: the embedder is constructed but never reached. Re-run is
untouched, since retrieval only ever shaped a plan's context.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add backend/src/econometrica/api/routers/runs.py backend/tests/api/test_runs.py
git commit -F /tmp/r6.txt && rm -f /tmp/r6.txt
```

---

## Task 7: Record retrieval is wired

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md**

Find the paragraph noting `services/rag.py` and `mcp/` "are still imported only by their own tests" and that "retrieval is missing an entire half (there is no route to add a document…)". Update it: retrieval is now wired end to end — a documents route (`POST/GET/DELETE /api/projects/{id}/documents`, PDF via `pypdf`) and a run that retrieves a project's passages into the Planner's context whenever the project has documents (documents-presence is the gate, no toggle). Record the seam: the `Retriever` protocol and `RetrievalOutcome` live in db-free `tools/retrieval.py` so `agents/` stays off `db.models`, while the concrete `ProjectRetriever` lives in `services/rag.py`; the trace step is `agent="planner"`, so no CHECK migration. Note that `mcp/` remains the one still imported only by its tests. Point at `docs/plans/2026-07-31-econometrica-retrieval-{design,implementation}.md`.

Also update the retrieval paragraphs (`Retrieval is scoped by a column…`, `Retrieved text never becomes a number`) only if they claim retrieval is unreachable; the scope and grounding facts still hold and should stay.

- [ ] **Step 2: Update README.md**

Update the section that describes reading the web for context to also mention that a project's uploaded documents are retrieved into the Planner's context (same grounding rule: nothing retrieved becomes a number). Keep it to the README's level of detail. The capability-map SVG is generated; leave it unless regenerating is trivial.

- [ ] **Step 3: Commit**

```bash
cat > /tmp/r7.txt <<'EOF'
docs: record document retrieval is wired end to end

services/rag.py was imported only by its own tests: no route added a
document, and no run retrieved. Both are closed now -- a documents route
and a run that retrieves a project's passages into the Planner's context
when the project has any. Records the db-free seam (Retriever protocol and
RetrievalOutcome in tools/retrieval so agents/ stays off db.models, the
concrete ProjectRetriever in services/rag), the documents-presence gate,
and that the trace step reuses agent="planner" so needs no migration. mcp/
is now the one thing still imported only by its tests.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
git add CLAUDE.md README.md
git commit -F /tmp/r7.txt && rm -f /tmp/r7.txt
```

---

## Final verification

From `backend/` (Postgres up):

- [ ] `uv run pytest -q` — full suite green (existing + new; live tests skip without Ollama; pre-existing live FRED tests may fail on network — unrelated).
- [ ] `uv run ruff check src tests alembic` — clean.
- [ ] `uv run mypy src` — clean.
- [ ] `uv run alembic check` — no drift (this feature adds **no** migration; the document tables already exist under `7441efabf4a6`).
- [ ] Optionally, with Ollama up: `uv run pytest -m live tests/services/test_rag.py -q` — the real embed→retrieve path (already present, unchanged).

---

## Self-review notes

- **Spec coverage.** Seam / db-free protocol (Task 1) ↔ design "the one hard constraint". Extraction + `pypdf` (Task 2) ↔ "Ingest / extract text". Routes + commit scar + 409/415/400/502 + embedder dep (Task 3) ↔ "Ingest — three routes". `ProjectRetriever` (Task 4) ↔ the concrete `Retriever`. `_retrieval_context`, Planner-only, degrade, `agent="planner"` (Task 5) ↔ "Run wiring". Documents-presence gate via EXISTS (Task 6) ↔ "The gate". Docs (Task 7) ↔ closing the CLAUDE.md open item. The grounding invariant and the live embed→retrieve probe already exist in `test_rag.py` and are preserved, matching the design's "the invariant stays" and "live probe".
- **Type consistency.** `Retriever.fetch(query) -> RetrievalOutcome`; `ProjectRetriever(session, project_id, embedder, *, limit=DEFAULT_LIMIT)` with `.model`; `Orchestrator.__init__(..., retriever: Retriever | None = None)`; `_retrieval_context(question, context, trace) -> str`; `extract_text(filename, data) -> str`; `get_embedder() -> Embedder`. `to_step_record()`'s `agent="planner"` needs no vocabulary change.
- **No placeholders.** Every code and test block is complete. The one soft spot — the PDF text fixture — is called out explicitly with two concrete options (a committed `one_line.pdf` fixture, or the blank-PDF parse-without-raising guard), not left as "add a test".
- **No migration.** The trace step reuses `agent="planner"`, the gate is documents-presence (no new column), and the document tables predate this work — so `alembic check` stays green with nothing added.
