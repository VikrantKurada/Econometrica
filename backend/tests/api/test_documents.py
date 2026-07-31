"""Adding, listing and removing a project's documents."""

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
    doc_id = (
        await client.post(f"/api/projects/{project}/documents", files=a_file())
    ).json()["id"]

    response = await client.delete(f"/api/documents/{doc_id}")

    assert response.status_code == 204
    count = await session.scalar(select(func.count()).select_from(DocumentChunk))
    assert count == 0
