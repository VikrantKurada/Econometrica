"""Persisting a run's trace."""

from sqlalchemy import select

from econometrica.agents.orchestrator import RunOutcome
from econometrica.agents.trace import StepRecord, tool_call_hash
from econometrica.db.models import Chat, Project, Run, Step
from econometrica.llm.types import Usage
from econometrica.services.tracing import record_run


async def make_chat(session) -> Chat:
    project = Project(name="Traces")
    chat = Chat(name="c", project=project)
    session.add(project)
    await session.flush()
    return chat


def outcome(**overrides) -> RunOutcome:
    payload = {
        "status": "completed",
        "question": "Does BTC follow a random walk?",
        "trace": [
            StepRecord(
                agent="planner",
                kind="llm",
                status="failed",
                provider="ollama",
                model="ministral-3:8b",
                attempt=1,
                usage=Usage(input_tokens=3900, output_tokens=700),
                latency_ms=4100.0,
                detail="reply rejected; retried",
            ),
            StepRecord(
                agent="planner",
                kind="llm",
                status="ok",
                parent=0,
                provider="ollama",
                model="ministral-3:8b",
                attempt=2,
                usage=Usage(input_tokens=3929, output_tokens=775),
                latency_ms=5000.0,
            ),
            StepRecord(
                agent="econometrician",
                kind="tool",
                status="ok",
                parent=1,
                tool="adf",
                tool_call_hash=tool_call_hash("adf", {"column": "BTC-USD"}),
                latency_ms=12.0,
            ),
        ],
    }
    payload.update(overrides)
    return RunOutcome.model_validate(payload)


async def test_a_run_and_its_steps_are_persisted(session):
    chat = await make_chat(session)

    run = await record_run(session, chat_id=chat.id, tier="critic", outcome=outcome())

    assert run.status == "completed"
    assert run.question == "Does BTC follow a random walk?"
    stored = list(await session.scalars(select(Step).order_by(Step.seq)))
    assert [step.agent for step in stored] == ["planner", "planner", "econometrician"]


async def test_steps_keep_the_order_the_run_produced_them_in(session):
    chat = await make_chat(session)

    run = await record_run(session, chat_id=chat.id, tier="critic", outcome=outcome())
    await session.refresh(run, ["steps"])

    assert [step.attempt for step in run.steps] == [1, 2, 1]


async def test_parent_links_survive_the_write(session):
    """Indices in the record become real foreign keys."""
    chat = await make_chat(session)

    run = await record_run(session, chat_id=chat.id, tier="critic", outcome=outcome())
    await session.refresh(run, ["steps"])
    first, retry, tool = run.steps

    assert first.parent_id is None
    assert retry.parent_id == first.id
    assert tool.parent_id == retry.id


async def test_a_rejected_attempt_is_its_own_step_with_its_own_cost(session):
    """Folding retries into one row understates every run that needed one."""
    chat = await make_chat(session)

    run = await record_run(session, chat_id=chat.id, tier="critic", outcome=outcome())
    await session.refresh(run, ["steps"])

    rejected = run.steps[0]
    assert rejected.status == "failed"
    assert rejected.input_tokens == 3900
    assert rejected.output_tokens == 700


async def test_run_totals_sum_their_steps(session):
    chat = await make_chat(session)

    run = await record_run(session, chat_id=chat.id, tier="critic", outcome=outcome())

    assert run.input_tokens == 3900 + 3929
    assert run.output_tokens == 700 + 775
    assert run.latency_ms == 4100.0 + 5000.0 + 12.0


async def test_a_failed_run_records_why_it_stopped(session):
    chat = await make_chat(session)

    run = await record_run(
        session,
        chat_id=chat.id,
        tier="single",
        outcome=outcome(status="failed", error="ProviderUnavailableError: daemon down", trace=[]),
    )

    await session.refresh(run, ["steps"])
    assert run.status == "failed"
    assert "daemon down" in run.error
    # A run that never got past the Planner still leaves a row saying so.
    assert run.steps == []


async def test_the_tier_is_recorded_alongside_the_run(session):
    chat = await make_chat(session)

    run = await record_run(session, chat_id=chat.id, tier="consensus", outcome=outcome())

    assert run.tier == "consensus"


async def test_tool_call_hashes_are_stable_and_tool_specific(session):
    """Two runs computing the same thing must be comparable; adf and kpss must not collide."""
    assert tool_call_hash("adf", {"column": "x"}) == tool_call_hash("adf", {"column": "x"})
    assert tool_call_hash("adf", {"column": "x"}) != tool_call_hash("kpss", {"column": "x"})
    assert tool_call_hash("adf", {"column": "x"}) != tool_call_hash("adf", {"column": "y"})


async def test_deleting_a_chat_takes_its_runs_with_it(session):
    chat = await make_chat(session)
    await record_run(session, chat_id=chat.id, tier="critic", outcome=outcome())

    await session.delete(chat)
    await session.flush()

    assert (await session.scalars(select(Run))).all() == []
    assert (await session.scalars(select(Step))).all() == []
