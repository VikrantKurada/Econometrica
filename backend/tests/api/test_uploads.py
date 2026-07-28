"""The upload endpoints, and the confirmation nobody may skip.

Three requests: post the file and get back a profile plus a suggested mapping;
optionally edit it; confirm it. The middle step is the point — §9 of the design
puts a person between what a profiler guessed and what gets stored, and these
tests are what stop a later refactor quietly removing them from the loop.

The blob is retained, also per §9, so a mapping can be revisited without asking
the user to upload the same file twice.
"""

from pathlib import Path

import pytest

from econometrica.api.deps import get_upload_store
from econometrica.main import app
from econometrica.services.uploads import UploadStore

WIDE = (
    "date,AAPL,MSFT\n"
    "2024-01-01,100.0,200.0\n"
    "2024-01-02,101.0,201.0\n"
    "2024-01-03,102.5,202.5\n"
    "2024-01-04,101.5,201.5\n"
)

AMBIGUOUS = (
    "date,v\n2024-01-01,1200\n2024-01-02,1300\n2024-01-03,1250\n2024-01-04,1400\n"
)


@pytest.fixture(autouse=True)
def store(tmp_path):
    """A store per test, so uploads never touch the real storage directory."""
    app.dependency_overrides[get_upload_store] = lambda: UploadStore(root=tmp_path)
    yield UploadStore(root=tmp_path)
    app.dependency_overrides.pop(get_upload_store, None)


async def make_project(client, name="Uploads"):
    return (await client.post("/api/projects", json={"name": name})).json()


async def upload(client, project_id, text: str = WIDE, name: str = "prices.csv"):
    return await client.post(
        f"/api/projects/{project_id}/uploads",
        files={"file": (name, text.encode(), "text/csv")},
    )


# --- posting a file -----------------------------------------------------------


async def test_uploading_returns_a_profile_and_a_proposal(client):
    project = await make_project(client)

    response = await upload(client, project["id"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["profile"]["rows"] == 4
    assert body["profile"]["layout"] == "wide"
    assert body["proposal"]["roles"]["date"] == "date"
    assert body["proposal"]["roles"]["AAPL"] == "price"
    assert body["confirmed"] is False


async def test_every_column_comes_back_with_its_candidates(client):
    """The confirmation screen has to offer alternatives, not just a verdict."""
    project = await make_project(client)

    body = (await upload(client, project["id"])).json()

    columns = {c["name"]: c for c in body["profile"]["columns"]}
    assert columns["AAPL"]["candidates"]
    assert all("reason" in c for c in columns["AAPL"]["candidates"])


async def test_an_upload_can_be_read_back(client):
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    response = await client.get(f"/api/uploads/{created['id']}")

    assert response.status_code == 200
    assert response.json()["profile"] == created["profile"]


async def test_an_unusable_file_is_refused_with_the_reason(client):
    project = await make_project(client)

    response = await client.post(
        f"/api/projects/{project['id']}/uploads",
        files={"file": ("one.csv", b"price\n100\n101\n", "text/csv")},
    )

    assert response.status_code == 422
    assert "one column" in response.json()["detail"]


async def test_uploading_to_a_project_that_does_not_exist_is_404(client):
    from uuid import uuid4

    response = await upload(client, str(uuid4()))

    assert response.status_code == 404


async def test_the_original_file_is_retained(client, store):
    """§9: the blob is kept. A mapping revisited a week later must not need the
    user to find the file again."""
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    blob = store.blob_path(created["id"])

    assert Path(blob).exists()
    assert Path(blob).read_text().startswith("date,AAPL,MSFT")


# --- confirming ---------------------------------------------------------------


async def test_confirming_a_mapping_reports_what_would_be_ingested(client):
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    response = await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "AAPL": "price", "MSFT": "price"}},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["confirmed"] is True
    assert body["observations"] == 8
    assert sorted(body["symbols"]) == ["AAPL", "MSFT"]
    assert body["fields"] == ["price"]


async def test_a_confirmation_may_differ_from_the_proposal(client):
    """The user edits. That is the entire reason the step exists."""
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    response = await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "AAPL": "price", "MSFT": "ignore"}},
    )

    assert response.json()["symbols"] == ["AAPL"]


async def test_confirming_names_a_column_the_file_lacks_is_422(client):
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    response = await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "GOOG": "price"}},
    )

    assert response.status_code == 422
    assert "GOOG" in response.json()["detail"]


async def test_confirming_without_a_date_column_is_422(client):
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    response = await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "ignore", "AAPL": "price"}},
    )

    assert response.status_code == 422
    assert "date" in response.json()["detail"]


async def test_confirming_an_upload_that_does_not_exist_is_404(client):
    from uuid import uuid4

    response = await client.post(
        f"/api/uploads/{uuid4()}/confirm", json={"roles": {"date": "date"}}
    )

    assert response.status_code == 404


async def test_the_confirmed_mapping_is_remembered(client):
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()
    await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "AAPL": "price", "MSFT": "ignore"}},
    )

    body = (await client.get(f"/api/uploads/{created['id']}")).json()

    assert body["confirmed"] is True
    assert body["mapping"]["roles"]["MSFT"] == "ignore"


# --- the model is optional ----------------------------------------------------


async def test_an_unambiguous_upload_consults_no_model(client):
    """No `column_mapper` is assigned on this project and none is needed. The
    common upload must not require one to be configured at all."""
    project = await make_project(client)

    response = await upload(client, project["id"])

    assert response.status_code == 201
    assert response.json()["consulted_model"] is False


async def test_an_ambiguous_upload_without_an_assigned_model_still_works(client):
    """It falls back to the profiler's own proposal rather than refusing. The
    user is about to confirm it anyway."""
    project = await make_project(client)

    response = await upload(client, project["id"], AMBIGUOUS, "v.csv")

    assert response.status_code == 201
    body = response.json()
    assert body["consulted_model"] is False
    assert body["proposal"]["ambiguous"] == ["v"]
    assert body["proposal"]["roles"]["v"] == "volume"


# --- confirming now ingests ---------------------------------------------------


async def test_confirming_creates_a_dataset_and_its_observations(client, session):
    """Task 6.7 validated a mapping and reported what it *would* ingest. With
    the store in place, confirming is what actually stores it."""
    from sqlalchemy import select

    from econometrica.db.models import Dataset, Observation

    project = await make_project(client)
    created = (await upload(client, project["id"])).json()

    response = await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "AAPL": "price", "MSFT": "price"}},
    )

    assert response.status_code == 200, response.text
    dataset = (await session.scalars(select(Dataset))).one()
    assert dataset.name == "prices.csv"
    assert dataset.column_roles["AAPL"] == "price"
    assert dataset.rows == 8
    assert len((await session.scalars(select(Observation))).all()) == 8
    assert response.json()["dataset_id"] == str(dataset.id)


async def test_the_dataset_label_names_the_file_and_is_not_synthetic(client, session):
    from sqlalchemy import select

    from econometrica.db.models import Dataset

    project = await make_project(client)
    created = (await upload(client, project["id"])).json()
    await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "AAPL": "price", "MSFT": "price"}},
    )

    dataset = (await session.scalars(select(Dataset))).one()

    assert "prices.csv" in dataset.source_label
    assert "synthetic" not in dataset.source_label.lower()


async def test_confirming_twice_replaces_rather_than_duplicates(client, session):
    """A user who realises they mapped a column wrongly confirms again. Leaving
    the first ingest behind would double every observation and the second
    mapping would never take effect."""
    from sqlalchemy import select

    from econometrica.db.models import Dataset, Observation

    project = await make_project(client)
    created = (await upload(client, project["id"])).json()
    for roles in (
        {"date": "date", "AAPL": "price", "MSFT": "price"},
        {"date": "date", "AAPL": "price", "MSFT": "ignore"},
    ):
        response = await client.post(
            f"/api/uploads/{created['id']}/confirm", json={"roles": roles}
        )
        assert response.status_code == 200, response.text

    assert len((await session.scalars(select(Dataset))).all()) == 1
    observations = (await session.scalars(select(Observation))).all()
    assert len(observations) == 4
    assert {row.symbol for row in observations} == {"AAPL"}


async def test_the_ingested_dataset_is_listed_for_the_project(client):
    project = await make_project(client)
    created = (await upload(client, project["id"])).json()
    await client.post(
        f"/api/uploads/{created['id']}/confirm",
        json={"roles": {"date": "date", "AAPL": "price", "MSFT": "price"}},
    )

    response = await client.get(f"/api/projects/{project['id']}/datasets")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "prices.csv"
    assert body[0]["rows"] == 8
    assert sorted(body[0]["symbols"]) == ["AAPL", "MSFT"]


async def test_an_unconfirmed_upload_is_not_listed_as_a_dataset(client):
    project = await make_project(client)
    await upload(client, project["id"])

    response = await client.get(f"/api/projects/{project['id']}/datasets")

    assert response.json() == []


async def test_confirming_commits_so_the_ingest_survives_the_request(client, session):
    """A flush is not a write, and the fixture cannot tell the difference.

    `client` overrides `get_session` with one session shared by every request
    in a test, so an uncommitted ingest stays visible to the next request and
    every assertion here passed while the real server discarded the rows the
    moment the request ended. The Phase 6 e2e found it: confirm returned 200,
    `GET /datasets` came back empty.

    Counting the commits is the only signal the shared-session fixture leaves,
    so that is what is asserted.
    """
    commits = 0
    original = session.commit

    async def counting_commit():
        nonlocal commits
        commits += 1
        await original()

    project = await make_project(client)
    posted = (await upload(client, project["id"])).json()

    # Counted from here only: creating the project commits too, and a counter
    # started earlier passes whatever this route does.
    session.commit = counting_commit  # type: ignore[method-assign]
    try:
        response = await client.post(
            f"/api/uploads/{posted['id']}/confirm",
            json={"roles": posted["proposal"]["roles"]},
        )
    finally:
        session.commit = original  # type: ignore[method-assign]

    assert response.status_code == 200
    assert commits >= 1, "confirming an upload must commit; a flush is rolled back"
