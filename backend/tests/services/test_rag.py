"""Documents chunked into pgvector, retrieved only within their project.

Two properties matter more than the retrieval quality, and both are tested here
rather than assumed.

**Scope is a security property.** A query must never see another project's
chunks. The scope column lives on the chunk itself rather than being reached
through a join, so the filter is on the same row being searched — a join can be
forgotten, a `WHERE` on the row cannot.

**Retrieved text never becomes a number.** This is the invariant the whole
project rests on: the grounding gate admits only values from a `ResultSet`, and
a document is not one. A narration citing a figure it read in a PDF is exactly
as ungrounded as one it invented, and has to be blocked the same way.
"""

from itertools import pairwise

import pytest
from sqlalchemy import select

from econometrica.db.models import DocumentChunk, Project
from econometrica.services.rag import (
    EmbeddingError,
    chunk_text,
    ingest_document,
    retrieve,
)


class FakeEmbedder:
    """Deterministic embeddings: similarity is decided by a keyword.

    A real model would make these tests about the model. What is under test is
    scoping and plumbing, so the vector is a function of the text.
    """

    model = "fake-embed"
    dimensions = 8

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        lowered = text.lower()
        # One axis per keyword; anything else lands on the last axis.
        keys = ["beta", "volatility", "carbon", "treasury", "momentum", "index", "risk"]
        vector = [1.0 if key in lowered else 0.0 for key in keys]
        return [*vector, 0.1]


async def make_project(session, name="P") -> Project:
    project = Project(name=name)
    session.add(project)
    await session.flush()
    return project


# --- chunking ------------------------------------------------------------------


def test_a_short_document_is_one_chunk():
    assert chunk_text("One sentence only.", size=200, overlap=20) == [
        "One sentence only."
    ]


def test_chunks_break_on_sentence_boundaries():
    """A chunk cut mid-sentence retrieves as a fragment that reads like a claim
    with its qualifier removed."""
    text = "Alpha was positive. Beta exceeded one. Volatility clustered strongly."

    chunks = chunk_text(text, size=40, overlap=0)

    assert all(chunk.endswith(".") for chunk in chunks)
    assert len(chunks) > 1


def test_chunks_overlap_so_a_boundary_does_not_hide_an_answer():
    text = " ".join(f"Sentence number {n}." for n in range(1, 13))

    chunks = chunk_text(text, size=60, overlap=30)

    # Consecutive chunks share text, so a passage spanning a cut is still found.
    assert any(set(a.split()) & set(b.split()) for a, b in pairwise(chunks))


def test_a_sentence_longer_than_the_chunk_size_is_still_emitted():
    """Refusing it would drop the content; splitting mid-word would corrupt it.
    Kept whole and oversized, which is the least bad of the three."""
    long_sentence = "word " * 200 + "end."

    chunks = chunk_text(long_sentence, size=50, overlap=10)

    assert len(chunks) >= 1
    assert "end." in chunks[-1]


def test_empty_text_produces_no_chunks():
    assert chunk_text("   \n  ", size=100, overlap=10) == []


def test_chunking_is_deterministic():
    text = "One. Two. Three. Four. Five. Six."

    assert chunk_text(text, size=20, overlap=5) == chunk_text(text, size=20, overlap=5)


# --- ingest ---------------------------------------------------------------------


async def test_a_document_is_stored_with_its_chunks(session):
    project = await make_project(session)
    embedder = FakeEmbedder()

    document = await ingest_document(
        session,
        project_id=project.id,
        name="notes.txt",
        text="Beta exceeded one. Volatility clustered strongly.",
        embedder=embedder,
        size=30,
        overlap=0,
    )

    chunks = (
        await session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == document.id)
        )
    ).all()
    assert len(chunks) >= 1
    assert document.chunks_count == len(chunks)


async def test_each_chunk_records_the_model_that_embedded_it(session):
    """Vectors from different models are not comparable, so a chunk that did
    not say which model produced it could be silently searched against the
    wrong space."""
    project = await make_project(session)

    await ingest_document(
        session,
        project_id=project.id,
        name="n.txt",
        text="Beta exceeded one.",
        embedder=FakeEmbedder(),
    )

    chunk = (await session.scalars(select(DocumentChunk))).first()
    assert chunk.embedding_model == "fake-embed"


async def test_a_document_that_cannot_be_embedded_is_refused_with_a_reason(session):
    """It fails at upload rather than at the first query, when the user is
    still looking at the file and can do something about it."""
    project = await make_project(session)
    embedder = FakeEmbedder(error=RuntimeError("the embedding model is not pulled"))

    with pytest.raises(EmbeddingError, match="not pulled"):
        await ingest_document(
            session,
            project_id=project.id,
            name="n.txt",
            text="Beta exceeded one.",
            embedder=embedder,
        )

    assert (await session.scalars(select(DocumentChunk))).all() == []


async def test_a_document_with_no_text_is_refused(session):
    project = await make_project(session)

    with pytest.raises(EmbeddingError, match="no text"):
        await ingest_document(
            session,
            project_id=project.id,
            name="blank.pdf",
            text="   ",
            embedder=FakeEmbedder(),
        )


# --- retrieval is scoped --------------------------------------------------------


async def test_a_query_never_returns_another_projects_chunks(session):
    """The security property. Scope is a column on the chunk, so the filter is
    on the same row being searched."""
    mine = await make_project(session, "Mine")
    theirs = await make_project(session, "Theirs")
    embedder = FakeEmbedder()

    await ingest_document(
        session,
        project_id=mine.id,
        name="a.txt",
        text="Beta exceeded one.",
        embedder=embedder,
    )
    await ingest_document(
        session,
        project_id=theirs.id,
        name="b.txt",
        text="Beta exceeded one in their study too.",
        embedder=embedder,
    )

    found = await retrieve(session, project_id=mine.id, query="beta", embedder=embedder)

    assert found
    assert {hit.document_name for hit in found} == {"a.txt"}


async def test_an_empty_project_retrieves_nothing_rather_than_everything(session):
    empty = await make_project(session, "Empty")
    other = await make_project(session, "Other")
    embedder = FakeEmbedder()
    await ingest_document(
        session,
        project_id=other.id,
        name="b.txt",
        text="Beta exceeded one.",
        embedder=embedder,
    )

    assert await retrieve(session, project_id=empty.id, query="beta", embedder=embedder) == []


async def test_retrieval_ranks_the_closer_chunk_first(session):
    project = await make_project(session)
    embedder = FakeEmbedder()
    await ingest_document(
        session,
        project_id=project.id,
        name="vol.txt",
        text="Volatility clustered.",
        embedder=embedder,
    )
    await ingest_document(
        session,
        project_id=project.id,
        name="carbon.txt",
        text="Carbon prices rose.",
        embedder=embedder,
    )

    found = await retrieve(session, project_id=project.id, query="volatility", embedder=embedder)

    assert found[0].document_name == "vol.txt"


async def test_retrieval_respects_its_limit(session):
    project = await make_project(session)
    embedder = FakeEmbedder()
    for index in range(5):
        await ingest_document(
            session,
            project_id=project.id,
            name=f"d{index}.txt",
            text="Beta exceeded one.",
            embedder=embedder,
        )

    found = await retrieve(
        session, project_id=project.id, query="beta", embedder=embedder, limit=2
    )

    assert len(found) == 2


async def test_deleting_a_project_takes_its_documents(session):
    project = await make_project(session)
    await ingest_document(
        session,
        project_id=project.id,
        name="a.txt",
        text="Beta exceeded one.",
        embedder=FakeEmbedder(),
    )

    await session.delete(project)
    await session.flush()

    assert (await session.scalars(select(DocumentChunk))).all() == []


# --- what a hit carries ---------------------------------------------------------


async def test_a_hit_is_attributable(session):
    """§9 wants retrieval attributed in the trace. A passage whose source
    cannot be named is a quotation from nowhere."""
    project = await make_project(session)
    embedder = FakeEmbedder()
    await ingest_document(
        session,
        project_id=project.id,
        name="notes.txt",
        text="Beta exceeded one.",
        embedder=embedder,
    )

    hit = (await retrieve(session, project_id=project.id, query="beta", embedder=embedder))[0]

    assert hit.document_name == "notes.txt"
    assert hit.ordinal == 0
    assert "Beta exceeded one." in hit.text
    assert 0.0 <= hit.score <= 1.0


# --- the concrete retriever -----------------------------------------------------


async def test_project_retriever_returns_an_attributed_outcome(session):
    from econometrica.services.rag import ProjectRetriever

    project = await make_project(session)
    embedder = FakeEmbedder()
    await ingest_document(
        session,
        project_id=project.id,
        name="notes.txt",
        text="Beta exceeded one.",
        embedder=embedder,
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


# --- the invariant --------------------------------------------------------------


async def test_a_number_read_from_a_document_is_still_ungrounded(session):
    """The invariant the whole project rests on. The grounding gate admits only
    values a tool computed, and a document is not a tool — so a narration citing
    a figure it read in a PDF is exactly as ungrounded as one it invented.

    Retrieval must not become a side channel into the one mechanical
    anti-hallucination check in the system.
    """
    from econometrica.agents.grounding import allowed_values, check_grounding
    from econometrica.econ.types import Estimate, Manifest, ResultSet

    project = await make_project(session)
    embedder = FakeEmbedder()
    await ingest_document(
        session,
        project_id=project.id,
        name="paper.txt",
        text="The published beta for this asset is 1.8100.",
        embedder=embedder,
    )
    hit = (await retrieve(session, project_id=project.id, query="beta", embedder=embedder))[0]
    assert "1.8100" in hit.text

    computed = ResultSet(
        tool="capm",
        version="1.0.0",
        params={},
        estimates=[Estimate(name="beta", value=1.273)],
        manifest=Manifest(data_fingerprint="a", tool="capm", tool_version="1.0.0"),
    )

    report = check_grounding("The beta is 1.8100.", allowed_values([computed]))

    assert report.grounded is False


# --- chunking must not lose text ------------------------------------------------


def test_chunking_never_drops_text():
    """The invariant a bug here broke. The first splitter matched sentences with
    a pattern that could fail to match part of the input and silently discard
    it: "The published beta is 1.8100." came back as "8100." — a number torn out
    of the sentence that qualified it, which is the worst thing a retrieval
    corpus can contain.
    """
    text = (
        "The published beta is 1.8100. It was estimated over 2018-2023. "
        "Dr. Smith notes the sample is short. See fig. 4 for the residuals."
    )

    chunks = chunk_text(text, size=40, overlap=0)

    rejoined = " ".join(chunks)
    for word in text.split():
        assert word in rejoined, f"{word!r} was dropped"


def test_a_decimal_point_does_not_end_a_sentence():
    chunks = chunk_text("The beta is 1.8100 exactly.", size=200, overlap=0)

    assert chunks == ["The beta is 1.8100 exactly."]


def test_an_abbreviation_keeps_its_sentence_together_when_it_can():
    """`Dr. Smith` splits, because no cheap rule tells it from a sentence end —
    but the text survives, which is the property that matters."""
    chunks = chunk_text("Dr. Smith agrees.", size=200, overlap=0)

    assert " ".join(chunks) == "Dr. Smith agrees."


# --- live -----------------------------------------------------------------------


def _ollama_is_up() -> bool:
    import httpx

    try:
        httpx.get("http://localhost:11434/api/tags", timeout=2.0)
    except httpx.HTTPError:
        return False
    return True


@pytest.mark.live
async def test_live_a_real_model_embeds_and_retrieves(session):
    """The whole path against a real embedding model: chunk, embed, store,
    and find the right passage by meaning rather than by keyword.

    The query shares no words with the passage it should return, so a match
    proves the vectors are doing the work.
    """
    from econometrica.llm.embeddings import OllamaEmbedder

    if not _ollama_is_up():
        pytest.skip("ollama is not running")

    embedder = OllamaEmbedder()
    project = await make_project(session, "Live")
    await ingest_document(
        session,
        project_id=project.id,
        name="vol.txt",
        text="Periods of large price swings tend to be followed by more large swings.",
        embedder=embedder,
    )
    await ingest_document(
        session,
        project_id=project.id,
        name="cook.txt",
        text="Preheat the oven and butter a shallow dish before adding the batter.",
        embedder=embedder,
    )

    found = await retrieve(
        session, project_id=project.id, query="volatility clustering", embedder=embedder
    )

    assert found[0].document_name == "vol.txt"
    assert found[0].score > 0


@pytest.mark.live
async def test_live_the_model_returns_the_width_the_schema_expects(session):
    """If this ever fails, the default model changed and every stored vector is
    in a different space from every new one."""
    from econometrica.db.models.document import EMBEDDING_DIMENSIONS
    from econometrica.llm.embeddings import OllamaEmbedder

    if not _ollama_is_up():
        pytest.skip("ollama is not running")

    vectors = await OllamaEmbedder().embed(["one"])

    assert len(vectors[0]) == EMBEDDING_DIMENSIONS
