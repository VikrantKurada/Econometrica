"""The metrics endpoint, and where each number is allowed to come from.

The design (§8) names what is tracked: latency percentiles, token spend by
provider and role, tool error rates, validator rejection rate, plan revision
counts, database timings. Half of those already live in `run_steps` and half
only exist as spans, and the important property is that **no number is summed
from both** — a cost that counts each model call twice is worse than no cost at
all, and it would look entirely plausible.
"""

from datetime import UTC, datetime, timedelta

import pytest

from econometrica.db.models import Chat, Project, Run, Span, Step
from econometrica.telemetry.metrics import collect_metrics


async def make_run(session, **overrides) -> Run:
    project = Project(name="Metrics")
    chat = Chat(name="C", project=project)
    session.add_all([project, chat])
    await session.flush()

    fields = {"chat_id": chat.id, "question": "q", "status": "completed", "tier": "critic"}
    fields.update(overrides)
    run = Run(**fields)
    session.add(run)
    await session.flush()
    return run


def step(run, **overrides) -> Step:
    fields = {
        "run_id": run.id,
        "agent": "planner",
        "kind": "llm",
        "status": "ok",
        "provider": "ollama",
        "model": "m",
        "input_tokens": 100,
        "output_tokens": 20,
        "latency_ms": 500.0,
        "cost_usd": 0.5,
    }
    fields.update(overrides)
    return Step(**fields)


def make_span(name="db.query", duration_ms=10.0, status="ok", offset=0) -> Span:
    started = datetime.now(UTC) - timedelta(seconds=offset)
    return Span(
        trace_id=f"{offset:032x}",
        span_id=f"{offset:016x}",
        name=name,
        kind="internal",
        status=status,
        started_at=started,
        ended_at=started + timedelta(milliseconds=duration_ms),
        duration_ms=duration_ms,
    )


# --- percentiles ---------------------------------------------------------------


async def test_percentiles_are_computed_over_a_known_distribution(session):
    """1..100ms, so the answers are arithmetic rather than a matter of taste."""
    session.add_all(
        make_span(name="db.query", duration_ms=float(ms), offset=ms)
        for ms in range(1, 101)
    )
    await session.flush()

    metrics = await collect_metrics(session)

    latency = next(entry for entry in metrics.spans if entry.name == "db.query")
    assert latency.count == 100
    assert latency.p50 == pytest.approx(50.5, abs=1.0)
    assert latency.p95 == pytest.approx(95.05, abs=1.0)
    assert latency.p99 == pytest.approx(99.01, abs=1.0)


async def test_span_names_are_reported_separately(session):
    """A p95 across handlers and database calls together describes nothing."""
    session.add_all(
        [
            make_span(name="http.request", duration_ms=100.0, offset=1),
            make_span(name="db.query", duration_ms=2.0, offset=2),
        ]
    )
    await session.flush()

    metrics = await collect_metrics(session)

    names = {entry.name: entry for entry in metrics.spans}
    assert names["http.request"].p50 == pytest.approx(100.0, abs=1.0)
    assert names["db.query"].p50 == pytest.approx(2.0, abs=1.0)


async def test_span_error_rates_are_reported(session):
    session.add_all(
        [
            make_span(name="tool.execute", status="ok", offset=1),
            make_span(name="tool.execute", status="error", offset=2),
        ]
    )
    await session.flush()

    metrics = await collect_metrics(session)

    entry = next(e for e in metrics.spans if e.name == "tool.execute")
    assert entry.error_rate == pytest.approx(0.5)


async def test_no_spans_is_not_an_error(session):
    metrics = await collect_metrics(session)

    assert metrics.spans == []
    assert metrics.runs == 0


# --- tokens come from one place ------------------------------------------------


async def test_tokens_are_summed_from_the_steps_only(session):
    """The property this whole module has to get right. `run_steps` is the
    record of what was billed; a span that also carried token counts would be
    summed alongside it and every figure would double."""
    run = await make_run(session)
    session.add_all([step(run), step(run, agent="narrator", output_tokens=80)])
    session.add(make_span(name="llm.request", offset=1))
    await session.flush()

    metrics = await collect_metrics(session)

    assert metrics.tokens.input == 200
    assert metrics.tokens.output == 100
    assert metrics.cost_usd == pytest.approx(1.0)


async def test_the_span_table_carries_no_token_columns():
    """Enforced structurally rather than by convention: there is no column for
    a future contributor to populate."""
    columns = set(Span.__table__.columns.keys())

    assert not {name for name in columns if "token" in name or "cost" in name}


async def test_tokens_are_broken_down_by_provider(session):
    run = await make_run(session)
    session.add_all(
        [
            step(run, provider="ollama", input_tokens=100),
            step(run, provider="anthropic", agent="validator", input_tokens=300),
        ]
    )
    await session.flush()

    metrics = await collect_metrics(session)

    by_provider = {entry.key: entry for entry in metrics.tokens_by_provider}
    assert by_provider["ollama"].input == 100
    assert by_provider["anthropic"].input == 300


async def test_tokens_are_broken_down_by_role(session):
    """Per-role assignment is a first-class feature, so per-role spend is the
    number that tells a user whether it was worth it."""
    run = await make_run(session)
    session.add_all(
        [step(run, agent="planner"), step(run, agent="narrator", input_tokens=700)]
    )
    await session.flush()

    metrics = await collect_metrics(session)

    by_agent = {entry.key: entry for entry in metrics.tokens_by_agent}
    assert by_agent["planner"].input == 100
    assert by_agent["narrator"].input == 700


# --- the pipeline's own rates ---------------------------------------------------


async def test_the_tool_error_rate_counts_tool_steps_only(session):
    """A failed model call is a retry; a failed tool is a result nobody got."""
    run = await make_run(session)
    session.add_all(
        [
            step(run, agent="econometrician", kind="tool", status="ok", tool="capm"),
            step(run, agent="econometrician", kind="tool", status="failed", tool="garch"),
            step(run, agent="planner", kind="llm", status="failed"),
        ]
    )
    await session.flush()

    metrics = await collect_metrics(session)

    assert metrics.tool_error_rate == pytest.approx(0.5)


async def test_the_validator_rejection_rate_is_reported(session):
    """A rejection is recorded as `refused` — the vocabulary `Step` already
    enforces, rather than a second word meaning the same thing."""
    run = await make_run(session)
    session.add_all(
        [
            step(run, agent="validator", kind="llm", status="ok"),
            step(run, agent="validator", kind="llm", status="refused"),
            step(run, agent="validator", kind="llm", status="refused"),
        ]
    )
    await session.flush()

    metrics = await collect_metrics(session)

    assert metrics.validator_rejection_rate == pytest.approx(2 / 3)


async def test_revision_counts_come_from_the_runs(session):
    await make_run(session, revisions=0)
    await make_run(session, revisions=2)
    await make_run(session, revisions=1)

    metrics = await collect_metrics(session)

    assert metrics.runs == 3
    assert metrics.revisions_total == 3
    assert metrics.revisions_mean == pytest.approx(1.0)


async def test_rates_with_no_denominator_are_none_not_zero(session):
    """Zero would read as "nothing ever failed", which is a claim. Nothing has
    run yet is a different statement and the dashboard should make it."""
    metrics = await collect_metrics(session)

    assert metrics.tool_error_rate is None
    assert metrics.validator_rejection_rate is None


# --- over the wire --------------------------------------------------------------


async def test_the_endpoint_serves_the_metrics(client, session):
    run = await make_run(session)
    session.add(step(run))
    session.add(make_span(offset=1))
    await session.flush()

    response = await client.get("/api/metrics")

    assert response.status_code == 200
    body = response.json()
    assert body["tokens"]["input"] == 100
    assert body["runs"] == 1
    assert any(entry["name"] == "db.query" for entry in body["spans"])
