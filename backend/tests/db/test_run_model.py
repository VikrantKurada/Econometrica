"""Run and Step persistence.

Every CHECK constraint here needs its own test. Alembic neither autogenerates
them nor verifies them, so this file is the only thing standing between a
hand-written migration and a column that quietly accepts anything.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError

from econometrica.db.models import Chat, Project, Run, Step


async def make_chat(session) -> Chat:
    project = Project(name="Traces")
    chat = Chat(name="c", project=project)
    session.add(project)
    await session.flush()
    return chat


async def make_run(session, **overrides) -> Run:
    chat = await make_chat(session)
    run = Run(chat_id=chat.id, question="Does BTC follow a random walk?", **overrides)
    session.add(run)
    await session.flush()
    return run


async def test_a_run_persists_with_sane_defaults(session):
    run = await make_run(session)

    assert run.id is not None
    assert run.status == "running"
    assert run.tier == "critic"
    assert run.revisions == 0
    assert run.input_tokens == 0
    assert run.cost_usd == 0.0


async def test_steps_belong_to_a_run_and_cascade_on_delete(session):
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="planner", kind="llm", status="ok"))
    await session.flush()

    await session.delete(run)
    await session.flush()

    remaining = await session.scalars(select(Step))
    assert remaining.all() == []


async def test_steps_order_on_an_identity_column_not_a_timestamp(session):
    """Steps written in one transaction share a created_at exactly.

    Ordering a trace on it comes back scrambled, which is the same reason
    Message carries `seq`.
    """
    run = await make_run(session)
    for agent in ("planner", "econometrician", "narrator"):
        session.add(Step(run_id=run.id, agent=agent, kind="llm", status="ok"))
    await session.flush()

    rows = list(await session.scalars(select(Step).order_by(Step.seq)))

    assert [row.agent for row in rows] == ["planner", "econometrician", "narrator"]
    assert rows[0].seq < rows[1].seq < rows[2].seq
    assert len({row.created_at for row in rows}) == 1


async def test_a_step_can_point_at_the_step_it_followed(session):
    run = await make_run(session)
    first = Step(run_id=run.id, agent="planner", kind="llm", status="failed")
    session.add(first)
    await session.flush()

    retry = Step(
        run_id=run.id, agent="planner", kind="llm", status="ok", parent_id=first.id
    )
    session.add(retry)
    await session.flush()

    assert retry.parent_id == first.id


async def test_deleting_a_parent_step_does_not_delete_its_children(session):
    """A trace with a hole is more useful than a trace missing a branch."""
    run = await make_run(session)
    parent = Step(run_id=run.id, agent="planner", kind="llm", status="ok")
    session.add(parent)
    await session.flush()
    child = Step(run_id=run.id, agent="narrator", kind="llm", status="ok",
                 parent_id=parent.id)
    session.add(child)
    await session.flush()

    await session.delete(parent)
    await session.flush()
    await session.refresh(child)

    assert child.parent_id is None


# --- the constraints alembic cannot see -------------------------------------


@pytest.mark.parametrize("status", ["running", "completed", "blocked", "failed"])
async def test_known_run_statuses_are_accepted(session, status):
    run = await make_run(session, status=status)
    assert run.status == status


async def test_an_unknown_run_status_is_rejected(session):
    with pytest.raises((IntegrityError, DBAPIError)):
        await make_run(session, status="vibes")


async def test_an_unknown_tier_is_rejected(session):
    with pytest.raises((IntegrityError, DBAPIError)):
        await make_run(session, tier="whenever")


async def test_a_negative_revision_count_is_rejected(session):
    with pytest.raises((IntegrityError, DBAPIError)):
        await make_run(session, revisions=-1)


async def test_negative_token_counts_are_rejected(session):
    with pytest.raises((IntegrityError, DBAPIError)):
        await make_run(session, input_tokens=-5)


async def test_a_blank_question_is_rejected(session):
    chat = await make_chat(session)
    session.add(Run(chat_id=chat.id, question="   "))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()


async def test_an_unknown_step_agent_is_rejected(session):
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="soothsayer", kind="llm", status="ok"))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()


async def test_an_unknown_step_kind_is_rejected(session):
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="planner", kind="telepathy", status="ok"))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()


async def test_an_unknown_step_status_is_rejected(session):
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="planner", kind="llm", status="probably"))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()


async def test_an_attempt_below_one_is_rejected(session):
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="planner", kind="llm", status="ok", attempt=0))
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()


async def test_a_step_cannot_be_its_own_parent(session):
    """A self-edge is not a DAG, and it makes the trace viewer loop forever."""
    run = await make_run(session)
    step = Step(run_id=run.id, agent="planner", kind="llm", status="ok")
    session.add(step)
    await session.flush()

    step.parent_id = step.id
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.flush()


async def test_a_quant_coder_step_is_accepted(session):
    """The escape hatch's steps have to reach the trace.

    This exercises the *model's* constraint against Postgres — the test
    database is built with `create_all`, not from the migrations, so it says
    nothing about whether the hand-written revision widening
    `ck_run_steps_agent_known` exists. That is
    `test_every_value_in_a_check_constraint_vocabulary_reaches_a_migration`'s
    job, and the two together are the whole gate: `alembic check` sees neither.
    """
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="quant_coder", kind="llm", status="ok"))

    await session.flush()


async def test_a_query_writer_step_is_accepted(session):
    """The query writer's billed turn has to reach the trace.

    Exercises the *model's* constraint against Postgres. Whether the
    hand-written migration widening `ck_run_steps_agent_known` exists is
    `test_every_value_in_a_check_constraint_vocabulary_reaches_a_migration`'s
    job — the test database is built with `create_all`, not the migrations.
    """
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="query_writer", kind="llm", status="ok"))

    await session.flush()


async def test_a_researcher_step_is_accepted(session):
    """The research agent's turns and MCP calls have to reach the trace."""
    run = await make_run(session)
    session.add(Step(run_id=run.id, agent="researcher", kind="llm", status="ok"))
    await session.flush()
