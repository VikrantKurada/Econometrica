"""`POST /api/chats/{id}/runs` — the multi-agent pipeline over SSE.

A separate route from Phase 3's chat endpoint on purpose: a different event
vocabulary, a different failure model, and a turn that can take minutes. The
chat route and its e2e gate are untouched by any of this.
"""

import json
from datetime import date
from uuid import uuid4

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from econometrica.api.deps import get_price_source, get_provider_registry
from econometrica.llm.fake import FakeProvider
from econometrica.main import app

QUESTION = "Does AAA follow a random walk?"

PLAN = {
    "question": QUESTION,
    "dataset": {"tickers": ["AAA"], "start": "2020-01-01", "end": "2020-06-30"},
    "steps": [{"id": "s1", "tool": "adf", "params": {"column": "AAA"}}],
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
