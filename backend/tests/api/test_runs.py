"""`POST /api/chats/{id}/runs` — the multi-agent pipeline over SSE.

A separate route from Phase 3's chat endpoint on purpose: a different event
vocabulary, a different failure model, and a turn that can take minutes. The
chat route and its e2e gate are untouched by any of this.
"""

import json
from datetime import date
from uuid import UUID, uuid4

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from econometrica.api.deps import get_price_source, get_provider_registry
from econometrica.data.base import DataUnavailableError
from econometrica.db.models import Dataset, Observation
from econometrica.llm.fake import FakeProvider
from econometrica.main import app

QUESTION = "Does AAA follow a random walk?"

PLAN = {
    "question": QUESTION,
    "dataset": {"tickers": ["AAA"], "start": "2020-01-01", "end": "2020-06-30"},
    "steps": [{"id": "s1", "tool": "adf", "params": {"column": "AAA"}}],
}
#: A plan whose tool emits a series, so the run has something to chart. `adf`
#: above returns scalars and diagnostics only.
VOL_PLAN = {
    **PLAN,
    "steps": [
        {"id": "s1", "tool": "realized_vol", "params": {"column": "AAA_return", "window": 20}}
    ],
}

APPROVED = json.dumps({"approved": True, "reasons": ["the diagnostics agree"]})
NARRATIVE = json.dumps({"prose": "The series wanders.", "citations": ["s1"]})


class FakeSource:
    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        index = pd.date_range("2020-01-01", periods=182, freq="D")
        rng = np.random.default_rng(7)
        return pd.Series(100.0 + np.cumsum(rng.normal(size=182)), index=index)


class ScriptedRegistry:
    """Hands every role the same scripted provider, consumed in call order.

    The script must match the tier exactly. Leaving a validator reply in it
    for a `single`-tier run does not go unused — the Narrator reads it next,
    fails to parse a verdict as a narrative, and quietly burns a retry.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.provider = FakeProvider(
            name="ollama", responses=responses or [json.dumps(PLAN), NARRATIVE]
        )

    def spec(self, name: str) -> object:
        if name != "ollama":
            raise KeyError(name)
        return object()

    def is_configured(self, name: str) -> bool:
        return name == "ollama"

    def build(self, name: str) -> FakeProvider:
        return self.provider


@pytest_asyncio.fixture
async def scripted():
    registry = ScriptedRegistry()
    app.dependency_overrides[get_provider_registry] = lambda: registry
    app.dependency_overrides[get_price_source] = lambda: FakeSource()
    yield registry
    app.dependency_overrides.pop(get_provider_registry, None)
    app.dependency_overrides.pop(get_price_source, None)


@pytest_asyncio.fixture
async def charting():
    """Like `scripted`, but the plan produces a result a chart can be drawn from."""
    registry = ScriptedRegistry([json.dumps(VOL_PLAN), NARRATIVE])
    app.dependency_overrides[get_provider_registry] = lambda: registry
    app.dependency_overrides[get_price_source] = lambda: FakeSource()
    yield registry
    app.dependency_overrides.pop(get_provider_registry, None)
    app.dependency_overrides.pop(get_price_source, None)


async def make_chat(client) -> str:
    project = (await client.post("/api/projects", json={"name": "Runs"})).json()
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
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()
    return str(chat["id"])


def events(body: str) -> list[dict]:
    parsed = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        name, data = "", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data = line[len("data:") :].strip()
        if data:
            parsed.append({"event": name, "data": json.loads(data)})
    return parsed


async def test_a_run_streams_its_phases_and_finishes(client, scripted):
    chat_id = await make_chat(client)

    response = await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    assert response.status_code == 200
    names = [event["event"] for event in events(response.text)]
    assert names[0] == "run.started"
    assert names[-1] == "run.finished"
    assert "plan.finished" in names
    assert "step.finished" in names
    assert "narrate.finished" in names


async def test_the_final_event_carries_the_outcome(client, scripted):
    chat_id = await make_chat(client)

    response = await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    final = events(response.text)[-1]["data"]["payload"]
    assert final["status"] == "completed"
    assert final["question"] == QUESTION
    assert final["narration"]["published"] is True


async def test_the_project_tier_is_honoured(client, scripted):
    """`single` was set on the project, so no validator turn is spent."""
    chat_id = await make_chat(client)

    response = await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    assert events(response.text)[-1]["data"]["payload"]["verdict"] is None
    # Planner and Narrator only — the validator script entry went unused.
    assert len(scripted.provider.calls) == 2


async def test_the_critic_tier_consults_the_validator(client, scripted):
    scripted.provider = FakeProvider(
        name="ollama", responses=[json.dumps(PLAN), APPROVED, NARRATIVE]
    )
    project = (await client.post("/api/projects", json={"name": "Critic"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={
            "validation_tier": "critic",
            "model_assignments": {
                role: {"provider": "ollama", "model": "fake-1"}
                for role in ("planner", "validator", "narrator")
            },
        },
    )
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()

    response = await client.post(
        f"/api/chats/{chat['id']}/runs", json={"question": QUESTION}
    )

    outcome = events(response.text)[-1]["data"]["payload"]
    assert outcome["verdict"]["approved"] is True
    assert len(scripted.provider.calls) == 3


async def test_a_finished_run_leaves_a_trace_in_the_database(client, scripted):
    """The run's own record, written after the stream, not during it."""
    chat_id = await make_chat(client)

    await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})

    runs = (await client.get(f"/api/chats/{chat_id}/runs")).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["tier"] == "single"
    assert runs[0]["question"] == QUESTION
    assert runs[0]["output_tokens"] > 0


async def test_the_trace_lists_its_steps_in_order(client, scripted):
    chat_id = await make_chat(client)
    await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})
    run_id = (await client.get(f"/api/chats/{chat_id}/runs")).json()[0]["id"]

    trace = (await client.get(f"/api/runs/{run_id}")).json()

    assert [step["agent"] for step in trace["steps"]] == [
        "planner",
        "data_steward",
        "econometrician",
        "narrator",
    ]
    assert trace["steps"][0]["parent_id"] is None
    assert trace["steps"][1]["parent_id"] == trace["steps"][0]["id"]


async def test_an_unknown_run_is_a_404(client, scripted):
    assert (await client.get(f"/api/runs/{uuid4()}")).status_code == 404


async def test_an_unknown_chat_is_a_404(client, scripted):
    response = await client.post(
        f"/api/chats/{uuid4()}/runs", json={"question": QUESTION}
    )
    assert response.status_code == 404


async def test_a_project_with_no_planner_assigned_refuses_before_running(client, scripted):
    project = (await client.post("/api/projects", json={"name": "Bare"})).json()
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()

    response = await client.post(
        f"/api/chats/{chat['id']}/runs", json={"question": QUESTION}
    )

    assert response.status_code == 503
    assert "planner" in response.json()["detail"].lower()


async def test_an_unconfigured_provider_refuses_before_running(client, scripted):
    project = (await client.post("/api/projects", json={"name": "Absent"})).json()
    await client.patch(
        f"/api/projects/{project['id']}",
        json={"model_assignments": {"planner": {"provider": "nope", "model": "m"}}},
    )
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()

    response = await client.post(
        f"/api/chats/{chat['id']}/runs", json={"question": QUESTION}
    )

    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


async def test_a_blank_question_is_rejected(client, scripted):
    chat_id = await make_chat(client)

    response = await client.post(f"/api/chats/{chat_id}/runs", json={"question": "   "})

    assert response.status_code == 422


@pytest.mark.parametrize("missing", ["narrator"])
async def test_every_model_role_must_be_assigned(client, scripted, missing):
    project = (await client.post("/api/projects", json={"name": "Partial"})).json()
    assignments = {
        "planner": {"provider": "ollama", "model": "fake-1"},
        "narrator": {"provider": "ollama", "model": "fake-1"},
    }
    assignments.pop(missing)
    await client.patch(
        f"/api/projects/{project['id']}", json={"model_assignments": assignments}
    )
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()

    response = await client.post(
        f"/api/chats/{chat['id']}/runs", json={"question": QUESTION}
    )

    assert response.status_code == 503
    assert missing in response.json()["detail"].lower()


# --- the artifacts a canvas reads ------------------------------------------


async def start_charted_run(client) -> tuple[str, str]:
    """A finished run whose one step produced a chartable result."""
    chat_id = await make_chat(client)
    await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})
    run_id = (await client.get(f"/api/chats/{chat_id}/runs")).json()[0]["id"]
    return chat_id, run_id


async def test_reading_a_run_returns_what_it_produced(client, charting):
    """Not just the step DAG: the plan, the results and the charts.

    Until this landed a run was only readable while its SSE stream was open,
    so a reload left the canvas with a trace and nothing to draw.
    """
    _, run_id = await start_charted_run(client)

    run = (await client.get(f"/api/runs/{run_id}")).json()

    assert run["outcome"]["plan"]["steps"][0]["tool"] == "realized_vol"
    assert run["outcome"]["execution"]["outcomes"][0]["result"]["tool"] == "realized_vol"
    assert run["outcome"]["charts"], "the canvas needs something to draw"
    assert run["outcome"]["charts"][0]["step_id"] == "s1"


async def test_the_data_quality_flags_survive_the_round_trip(client, charting):
    # The synthetic_data flag is the one a canvas must never lose: rendering
    # generated prices as though they were market data is the failure the Data
    # Steward exists to prevent.
    _, run_id = await start_charted_run(client)

    run = (await client.get(f"/api/runs/{run_id}")).json()

    assert "flags" in run["outcome"]["quality"]


async def test_listing_runs_does_not_carry_every_series(client, charting):
    # A run's series are tens to hundreds of KB. A list of runs that dragged
    # them along would make the sidebar the most expensive request in the app.
    chat_id, _ = await start_charted_run(client)

    listed = (await client.get(f"/api/chats/{chat_id}/runs")).json()

    assert "outcome" not in listed[0]


# --- re-running from the manifest ------------------------------------------


class ShiftedSource:
    """The same shape of data, different numbers — a different fingerprint."""

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        index = pd.date_range("2020-01-01", periods=182, freq="D")
        rng = np.random.default_rng(11)
        return pd.Series(100.0 + np.cumsum(rng.normal(size=182)), index=index)


async def test_rerunning_a_run_reproduces_its_results(client, charting):
    """The last open item in the parent plan's definition of done.

    A manifest that cannot be re-run is a promise the project does not keep.
    """
    _, run_id = await start_charted_run(client)

    report = (await client.post(f"/api/runs/{run_id}/rerun")).json()

    assert report["reproduced"] is True
    assert [step["step_id"] for step in report["steps"]] == ["s1"]
    step = report["steps"][0]
    assert step["data_fingerprint"] == step["original_data_fingerprint"]
    assert step["params_hash"] == step["original_params_hash"]


async def test_a_rerun_asks_no_model_anything(client, charting):
    # Reproduction re-executes the recorded plan; it does not re-plan. If it
    # called a model the script would run dry and the provider would raise, so
    # this asserts the count as well as the outcome.
    _, run_id = await start_charted_run(client)
    spent = len(charting.provider.calls)

    response = await client.post(f"/api/runs/{run_id}/rerun")

    assert response.status_code == 200
    assert len(charting.provider.calls) == spent


async def test_data_that_changed_underneath_is_reported_not_hidden(client, charting):
    # The failure mode worth catching: a source that quietly revises history.
    # Saying "reproduced" here would make the manifest worthless.
    _, run_id = await start_charted_run(client)
    app.dependency_overrides[get_price_source] = lambda: ShiftedSource()

    report = (await client.post(f"/api/runs/{run_id}/rerun")).json()

    assert report["reproduced"] is False
    step = report["steps"][0]
    assert step["data_fingerprint"] != step["original_data_fingerprint"]
    assert "fingerprint" in step["detail"]


async def test_a_run_that_never_planned_cannot_be_rerun(client, scripted):
    # A run that died before planning has no manifest to re-run, and saying so
    # is better than returning an empty report that looks like agreement.
    chat_id = await make_chat(client)
    scripted.provider.responses = ["not json at all", "still not json"]
    await client.post(f"/api/chats/{chat_id}/runs", json={"question": QUESTION})
    run_id = (await client.get(f"/api/chats/{chat_id}/runs")).json()[0]["id"]

    response = await client.post(f"/api/runs/{run_id}/rerun")

    assert response.status_code == 409
    assert "no plan" in response.json()["detail"]


async def test_rerunning_an_unknown_run_is_a_404(client, scripted):
    assert (await client.post(f"/api/runs/{uuid4()}/rerun")).status_code == 404


# --- reading the project's own uploads ----------------------------------------

UPLOAD_PLAN = {
    "question": "Describe LONDON",
    "dataset": {"tickers": ["LONDON"], "start": "2024-01-01", "end": "2024-06-30"},
    "steps": [{"id": "s1", "tool": "adf", "params": {"column": "LONDON"}}],
}


class EmptyMarket:
    """Knows nothing.

    Deliberately not a source that merely happens to lack LONDON: if a run
    resolves against this, it resolved from the upload and there is no other
    reading available.
    """

    label = "Fake Market (dividend-adjusted)"

    async def prices(self, ticker: str, *, start: date, end: date) -> pd.Series:
        raise DataUnavailableError(f"{ticker}: not listed")


@pytest_asyncio.fixture
async def uploading():
    registry = ScriptedRegistry([json.dumps(UPLOAD_PLAN), NARRATIVE])
    app.dependency_overrides[get_provider_registry] = lambda: registry
    app.dependency_overrides[get_price_source] = lambda: EmptyMarket()
    yield registry
    app.dependency_overrides.pop(get_provider_registry, None)
    app.dependency_overrides.pop(get_price_source, None)


async def make_project_and_chat(client) -> tuple[str, str]:
    """As `make_chat`, but hands back the project id the upload needs."""
    project = (await client.post("/api/projects", json={"name": "Uploads"})).json()
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
    chat = (
        await client.post(f"/api/projects/{project['id']}/chats", json={"name": "c"})
    ).json()
    return str(project["id"]), str(chat["id"])


async def ingest_london(session, project_id: str) -> Dataset:
    """180 daily levels under the symbol LONDON, shaped the way ingest writes."""
    dataset = Dataset(
        project_id=UUID(project_id),
        name="hpi.csv",
        filename="hpi.csv",
        blob_path="uploads/hpi.csv",
        source_label="upload: hpi.csv (ingested 2024-01-05)",
        fingerprint="a" * 64,
        rows=180,
        column_roles={"date": "date", "LONDON": "price"},
    )
    session.add(dataset)
    await session.flush()

    days = pd.date_range("2024-01-01", periods=180, freq="D")
    rng = np.random.default_rng(11)
    values = 500.0 + np.cumsum(rng.normal(size=180))
    session.add_all(
        Observation(
            dataset_id=dataset.id,
            ts=day.tz_localize("UTC").to_pydatetime(),
            symbol="LONDON",
            field="price",
            value=float(value),
        )
        for day, value in zip(days, values, strict=True)
    )
    await session.flush()
    return dataset


async def test_a_run_resolves_a_symbol_from_the_projects_upload(
    client, session, uploading
):
    """The end the whole upload path was built for.

    Asserted through the route rather than against the steward, because the
    wiring is the thing that was missing — every piece below it already worked.
    """
    project_id, chat_id = await make_project_and_chat(client)
    await ingest_london(session, project_id)

    async with client.stream(
        "POST", f"/api/chats/{chat_id}/runs", json={"question": "Describe LONDON"}
    ) as response:
        body = "".join([chunk async for chunk in response.aiter_text()])

    finished = [e for e in events(body) if e["event"] == "run.finished"]
    assert finished, body
    outcome = finished[0]["data"]["payload"]
    assert outcome["status"] == "completed", outcome["error"]
    assert outcome["quality"]["tickers"] == ["LONDON"]
    assert "upload: hpi.csv" in outcome["quality"]["source"]


async def start_upload_run(client, session) -> str:
    """Run once against an uploaded series and hand back the run id."""
    project_id, chat_id = await make_project_and_chat(client)
    await ingest_london(session, project_id)

    async with client.stream(
        "POST", f"/api/chats/{chat_id}/runs", json={"question": "Describe LONDON"}
    ) as response:
        async for _ in response.aiter_text():
            pass

    runs = (await client.get(f"/api/chats/{chat_id}/runs")).json()
    return str(runs[0]["id"])


async def test_rerun_reproduces_an_uploaded_series_from_the_upload(
    client, session, uploading
):
    """The manifest claim, at the point it would silently break.

    `rerun` took the globally configured source and never looked at the
    project, so a run built on a file re-resolved from the market source and
    reported on data the original never saw. Here that source cannot serve
    LONDON at all, so a reproducing re-run is only reachable via the upload.
    """
    uploading.provider.responses = [json.dumps(UPLOAD_PLAN), NARRATIVE]
    run_id = await start_upload_run(client, session)

    response = await client.post(f"/api/runs/{run_id}/rerun")

    assert response.status_code == 200, response.text
    assert response.json()["reproduced"] is True


async def test_rerun_answers_409_when_the_dataset_is_gone(client, session, uploading):
    from sqlalchemy import delete

    run_id = await start_upload_run(client, session)
    await session.execute(delete(Dataset))
    await session.flush()

    response = await client.post(f"/api/runs/{run_id}/rerun")

    # A run whose data is gone is a finding, not a crash.
    assert response.status_code == 409
    assert "LONDON" in response.json()["detail"]
