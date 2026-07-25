"""Tests for message history and the streaming chat endpoint.

The endpoint's contract is that the transcript survives whatever the provider
does. A reply that streams fine, one that fails halfway, one from a provider
that was never configured — all three must leave the conversation readable
afterwards, because a chat that silently loses turns is unusable.
"""

import json
import re

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from econometrica.llm.errors import ProviderUnavailableError
from econometrica.llm.fake import FakeProvider
from econometrica.llm.registry import ProviderRegistry
from econometrica.main import app
from econometrica.services.keystore import KeyStore

SECRET = "0123456789abcdef0123456789abcdef"


def parse_sse(payload: str) -> list[dict]:
    """Decode an SSE body into ``{event, data}`` dicts."""
    events = []
    for block in re.split(r"\r?\n\r?\n", payload.strip()):
        if not block.strip():
            continue
        parsed: dict = {}
        for line in re.split(r"\r?\n", block):
            if line.startswith("event:"):
                parsed["event"] = line[len("event:") :].strip()
            elif line.startswith("data:"):
                parsed["data"] = json.loads(line[len("data:") :].strip())
        if parsed:
            events.append(parsed)
    return events


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider(name="ollama", responses=["The beta is 1.3."])


@pytest_asyncio.fixture
async def chat_client(session, tmp_path, fake_provider):
    """A client whose provider registry yields one scripted fake."""
    from econometrica.api.deps import get_provider_registry
    from econometrica.db.session import get_session

    keystore = KeyStore(path=tmp_path / "keys.enc", secret=SECRET)
    registry = ProviderRegistry(keystore=keystore, factories={})
    registry.factories = {
        name: (lambda api_key: fake_provider) for name in registry.names()
    }

    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_provider_registry] = lambda: registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_provider_registry, None)


@pytest_asyncio.fixture
async def chat_id(chat_client) -> str:
    project = (await chat_client.post("/api/projects", json={"name": "P"})).json()
    chat = (
        await chat_client.post(f"/api/projects/{project['id']}/chats", json={"name": "C"})
    ).json()
    return str(chat["id"])


def _send(content: str = "what is the beta?") -> dict:
    return {"content": content, "provider": "ollama", "model": "llama3.2:latest"}


# --- history ----------------------------------------------------------------


async def test_a_new_chat_has_no_messages(chat_client, chat_id):
    response = await chat_client.get(f"/api/chats/{chat_id}/messages")
    assert response.status_code == 200
    assert response.json() == []


async def test_history_of_an_unknown_chat_is_404(chat_client):
    response = await chat_client.get(
        "/api/chats/00000000-0000-0000-0000-000000000000/messages"
    )
    assert response.status_code == 404


# --- streaming --------------------------------------------------------------


async def test_streaming_yields_deltas_that_reassemble_into_the_reply(
    chat_client, chat_id
):
    response = await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    assert response.status_code == 200
    events = parse_sse(response.text)
    deltas = [e["data"]["text"] for e in events if e["event"] == "delta"]
    assert "".join(deltas) == "The beta is 1.3."


async def test_the_stream_opens_with_the_persisted_user_message(chat_client, chat_id):
    """The client needs the id immediately to reconcile its optimistic render."""
    response = await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    events = parse_sse(response.text)
    assert events[0]["event"] == "start"
    assert events[0]["data"]["user_message"]["content"] == "what is the beta?"
    assert events[0]["data"]["user_message"]["role"] == "user"


async def test_the_stream_closes_with_the_complete_assistant_message(
    chat_client, chat_id
):
    response = await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    final = parse_sse(response.text)[-1]
    assert final["event"] == "done"
    message = final["data"]["message"]
    assert message["content"] == "The beta is 1.3."
    assert message["provider"] == "ollama"
    assert message["model"] == "llama3.2:latest"
    assert message["output_tokens"] > 0


async def test_both_turns_are_persisted_in_order(chat_client, chat_id):
    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    history = (await chat_client.get(f"/api/chats/{chat_id}/messages")).json()

    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "what is the beta?"
    assert history[1]["content"] == "The beta is 1.3."


async def test_prior_turns_are_sent_back_to_the_provider(
    chat_client, chat_id, fake_provider
):
    """Without history the model has amnesia between turns."""
    fake_provider.responses = ["first reply", "second reply"]

    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send("one"))
    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send("two"))

    second_call = fake_provider.calls[-1]
    assert [m.content for m in second_call.messages] == [
        "one",
        "first reply",
        "two",
    ]


async def test_usage_is_recorded_for_cost_accounting(chat_client, chat_id):
    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    assistant = (await chat_client.get(f"/api/chats/{chat_id}/messages")).json()[1]
    assert assistant["input_tokens"] > 0
    assert assistant["output_tokens"] > 0
    assert assistant["latency_ms"] >= 0


# --- failure paths ----------------------------------------------------------


async def test_a_provider_failure_does_not_lose_the_user_message(
    chat_client, chat_id, fake_provider
):
    """The question the user typed must survive a backend failure."""
    fake_provider.error = ProviderUnavailableError("ollama", "daemon not running")

    response = await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    events = parse_sse(response.text)
    assert events[-1]["event"] == "error"
    assert "daemon not running" in events[-1]["data"]["detail"]

    history = (await chat_client.get(f"/api/chats/{chat_id}/messages")).json()
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "what is the beta?"


async def test_a_failed_generation_is_recorded_rather_than_dropped(
    chat_client, chat_id, fake_provider
):
    """A visible failed turn beats a transcript that silently skips one."""
    fake_provider.error = ProviderUnavailableError("ollama", "daemon not running")

    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    history = (await chat_client.get(f"/api/chats/{chat_id}/messages")).json()
    assert len(history) == 2
    assert history[1]["role"] == "assistant"
    assert history[1]["error"]
    assert "daemon not running" in history[1]["error"]


async def test_an_unconfigured_provider_is_refused_before_anything_is_written(
    chat_client, chat_id
):
    """No point persisting a turn that cannot possibly be answered."""
    response = await chat_client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "hi", "provider": "openai", "model": "gpt-4o"},
    )

    assert response.status_code == 503
    assert "key" in response.text.lower()

    history = (await chat_client.get(f"/api/chats/{chat_id}/messages")).json()
    assert history == []


async def test_an_unknown_provider_is_a_404(chat_client, chat_id):
    response = await chat_client.post(
        f"/api/chats/{chat_id}/messages",
        json={"content": "hi", "provider": "nope", "model": "x"},
    )
    assert response.status_code == 404


async def test_posting_to_an_unknown_chat_is_404(chat_client):
    response = await chat_client.post(
        "/api/chats/00000000-0000-0000-0000-000000000000/messages", json=_send()
    )
    assert response.status_code == 404


async def test_blank_content_is_rejected(chat_client, chat_id):
    response = await chat_client.post(
        f"/api/chats/{chat_id}/messages", json=_send("   ")
    )
    assert response.status_code == 422


async def test_deleting_a_chat_removes_its_messages(chat_client, chat_id):
    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    assert (await chat_client.delete(f"/api/chats/{chat_id}")).status_code == 204
    assert (await chat_client.get(f"/api/chats/{chat_id}/messages")).status_code == 404


# --- response shape ---------------------------------------------------------


async def test_the_stream_is_served_as_server_sent_events(chat_client, chat_id):
    response = await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())
    assert response.headers["content-type"].startswith("text/event-stream")


async def test_history_never_exposes_internal_columns(chat_client, chat_id):
    await chat_client.post(f"/api/chats/{chat_id}/messages", json=_send())

    message = (await chat_client.get(f"/api/chats/{chat_id}/messages")).json()[0]
    assert set(message) == {
        "id",
        "chat_id",
        "seq",
        "role",
        "content",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "latency_ms",
        "stop_reason",
        "error",
        "created_at",
    }
