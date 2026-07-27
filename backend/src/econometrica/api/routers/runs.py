"""The multi-agent pipeline, streamed.

A separate route from `POST /api/chats/{id}/messages` rather than a mode flag
on it. The two differ in every way that matters to a client: a different event
vocabulary (phases, not tokens), a different failure model (a run that is
refused still returns results), and a turn that can take minutes. Folding them
together would make one route's response shape depend on a request field,
which neither the frontend's stream reader nor its tests can narrow on — and
it would put Phase 3's e2e gate at risk for no gain.

Roles are bound from `Project.model_assignments` and the tier from
`Project.validation_tier`, both validated **before** anything runs. A run that
cannot be completed should not start, for the same reason `messages.py`
refuses an unconfigured provider before writing the user's turn.
"""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from econometrica.agents.data_steward import DataSteward
from econometrica.agents.econometrician import Econometrician, StepOutcome
from econometrica.agents.narrator import Narrator
from econometrica.agents.orchestrator import TIERS, Orchestrator, RunOutcome, Tier
from econometrica.agents.planner import Planner
from econometrica.agents.quant_coder import QuantCoder
from econometrica.agents.schemas import AnalysisPlan
from econometrica.agents.validator import Validator
from econometrica.api.deps import (
    FactorSourceDep,
    PriceSourceDep,
    ProviderRegistryDep,
    RateSourceDep,
    SessionDep,
    get_chat_or_404,
    get_project_or_404,
)
from econometrica.db.models import Chat, Project, Run
from econometrica.econ.types import ResultSet
from econometrica.llm.base import LLMProvider
from econometrica.llm.registry import ProviderRegistry
from econometrica.schemas.run import (
    RerunReport,
    RunDetail,
    RunRead,
    RunStart,
    StepReproduction,
)
from econometrica.services.capabilities import resolve_capabilities
from econometrica.services.tracing import record_run

router = APIRouter(prefix="/api/chats", tags=["runs"])
#: Reading a trace is not scoped to a chat — a run id is enough, and the trace
#: viewer opens one from a link rather than by browsing.
traces = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("/{chat_id}/runs", response_model=list[RunRead])
async def list_runs(chat_id: UUID, session: SessionDep) -> list[Run]:
    await get_chat_or_404(session, chat_id)
    rows = await session.scalars(
        select(Run).where(Run.chat_id == chat_id).order_by(Run.created_at.desc())
    )
    return list(rows)


@traces.get("/{run_id}", response_model=RunDetail)
async def read_run(run_id: UUID, session: SessionDep) -> Run:
    """One run and its steps, ordered by `Step.seq`.

    Eager-loaded: touching the relationship after the response has begun
    raises MissingGreenlet rather than emitting a lazy SELECT.
    """
    run = await session.get(Run, run_id, options=[selectinload(Run.steps)])
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found"
        )
    return run

@traces.post("/{run_id}/rerun", response_model=RerunReport)
async def rerun(
    run_id: UUID,
    session: SessionDep,
    source: PriceSourceDep,
    rate_source: RateSourceDep,
    factor_source: FactorSourceDep,
) -> RerunReport:
    """Re-execute a recorded plan and report whether it reproduced.

    The whole claim this project makes about its numbers is that they can be
    got back, so this asks exactly that and nothing more: the recorded plan,
    freshly resolved data, the same tools. No model is consulted — re-planning
    would test whether a model repeats itself, which is a different question
    and not one the manifest makes any promise about.

    Disagreement is reported per step rather than raised. A source that has
    quietly revised its history is a finding about the data, and the useful
    answer names which step stopped matching and why.
    """
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found"
        )

    recorded: dict[str, Any] = run.outcome or {}
    if not recorded.get("plan"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "this run recorded no plan, so there is nothing to re-run."
                " Runs that failed before planning cannot be reproduced."
            ),
        )

    plan = AnalysisPlan.model_validate(recorded["plan"])
    before = {
        step["step_id"]: step
        for step in (recorded.get("execution") or {}).get("outcomes", [])
    }

    dataset = await DataSteward(
        source, rate_source=rate_source, factor_source=factor_source
    ).resolve(plan.dataset)
    execution = await Econometrician().run(plan, dataset.frame)

    steps = [_compare(fresh, before.get(fresh.step_id)) for fresh in execution.outcomes]
    return RerunReport(
        run_id=run_id,
        # An empty run reproduces nothing; saying `True` for it would be the
        # most misleading answer available.
        reproduced=bool(steps) and all(step.reproduced for step in steps),
        steps=steps,
    )


def _compare(fresh: StepOutcome, before: dict[str, Any] | None) -> StepReproduction:
    """One step, then and now."""
    recorded = (before or {}).get("result")
    original = ResultSet.model_validate(recorded) if recorded else None
    was = manifest_of(original)
    now = manifest_of(fresh.result)

    problems: list[str] = []
    if before is None:
        problems.append("this step was not in the recorded run")
    elif fresh.status != before["status"]:
        problems.append(f"the step {before['status']} before and {fresh.status} now")

    if was["data_fingerprint"] != now["data_fingerprint"]:
        problems.append(
            "the data fingerprint changed, so the source is not serving the same history"
        )
    if was["params_hash"] != now["params_hash"]:
        problems.append("the parameters hash differently")
    if was["tool_version"] != now["tool_version"]:
        problems.append(f"the tool moved from {was['tool_version']} to {now['tool_version']}")

    # The fingerprints agreeing is necessary, not sufficient: the numbers are
    # the thing being reproduced, so they are compared directly.
    if (
        original is not None
        and fresh.result is not None
        and original.all_numeric_values() != fresh.result.all_numeric_values()
    ):
        problems.append("the numbers differ from the ones recorded")

    return StepReproduction(
        step_id=fresh.step_id,
        tool=fresh.tool,
        reproduced=not problems,
        status=fresh.status,
        original_status=str((before or {}).get("status", "")),
        data_fingerprint=now["data_fingerprint"],
        original_data_fingerprint=was["data_fingerprint"],
        params_hash=now["params_hash"],
        original_params_hash=was["params_hash"],
        detail="; ".join(problems),
    )


def manifest_of(result: ResultSet | None) -> dict[str, str]:
    """The identifying parts of a manifest, or blanks where there is no result."""
    if result is None:
        return {"data_fingerprint": "", "params_hash": "", "tool_version": ""}
    return {
        "data_fingerprint": result.manifest.data_fingerprint,
        "params_hash": result.manifest.params_hash,
        "tool_version": result.manifest.tool_version,
    }


#: Roles that need a model assigned. The Data Steward and Econometrician are
#: deterministic, so they are absent by design rather than by omission.
REQUIRED_ROLES = ("planner", "narrator")
OPTIONAL_ROLES = ("validator",)


@router.post("/{chat_id}/runs")
async def start_run(
    chat_id: UUID,
    payload: RunStart,
    session: SessionDep,
    registry: ProviderRegistryDep,
    source: PriceSourceDep,
    rate_source: RateSourceDep,
    factor_source: FactorSourceDep,
) -> EventSourceResponse:
    """Run the full pipeline for one question, streaming progress as SSE."""
    chat = await get_chat_or_404(session, chat_id)
    project = await get_project_or_404(session, chat.project_id)

    orchestrator = _build(project, chat, registry, source, rate_source, factor_source)

    async def events() -> AsyncIterator[dict[str, str]]:
        outcome: RunOutcome | None = None

        async for event in orchestrator.stream(payload.question, context=payload.context):
            if event.name == "run.finished":
                outcome = RunOutcome.model_validate(event.payload)
            # The name goes in the SSE `event:` field, as `messages.py` does,
            # so repeating it in the body would just be noise on the wire.
            yield {
                "event": event.name,
                "data": event.model_dump_json(exclude={"name"}),
            }

        if outcome is None:
            return

        # Persist after streaming, not during: the trace is only complete once
        # the run is, and a write that failed mid-stream would leave a partial
        # trace claiming to be whole. A failure to record must not retract a
        # run the client already watched succeed, so it is reported and the
        # stream still ends cleanly.
        try:
            await record_run(
                session, chat_id=chat_id, tier=orchestrator.tier, outcome=outcome
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            yield {
                "event": "run.untraced",
                "data": json.dumps(
                    {"detail": f"the run completed but its trace was not saved: {exc}"}
                ),
            }

    return EventSourceResponse(events())


# --- internals --------------------------------------------------------------


def _build(
    project: Project,
    chat: Chat,
    registry: ProviderRegistry,
    source: PriceSourceDep,
    rate_source: PriceSourceDep,
    factor_source: FactorSourceDep,
) -> Orchestrator:
    planner_provider, planner_model = _bind("planner", project, registry)
    narrator_provider, narrator_model = _bind("narrator", project, registry)

    tier = _tier(project)
    validator = None
    if tier != "single" and "validator" in (project.model_assignments or {}):
        provider, model = _bind("validator", project, registry)
        validator = Validator(provider, model)

    # Read through the resolver rather than off the column, even though the
    # sandbox is the one capability a chat cannot override: the rule about
    # which scope decides belongs in `services.capabilities` and nowhere else.
    capabilities = resolve_capabilities(project, chat)
    coder = None
    if capabilities.code_sandbox and "quant_coder" in (project.model_assignments or {}):
        provider, model = _bind("quant_coder", project, registry)
        coder = QuantCoder(provider, model)

    return Orchestrator(
        planners=[
            Planner(planner_provider, planner_model, code_sandbox=capabilities.code_sandbox)
        ],
        steward=DataSteward(
            source, rate_source=rate_source, factor_source=factor_source
        ),
        validator=validator,
        narrator=Narrator(narrator_provider, narrator_model),
        coder=coder,
        code_sandbox=capabilities.code_sandbox,
        tier=tier,
    )


def _tier(project: Project) -> Tier:
    value = project.validation_tier
    if value not in TIERS:
        # The column has no CHECK constraint, so an unknown value is possible
        # and must not surface as a 500 from deep inside the pipeline.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"project validation_tier {value!r} is not one of {', '.join(TIERS)}",
        )
    return value


def _bind(
    role: str, project: Project, registry: ProviderRegistry
) -> tuple[LLMProvider, str]:
    assignments: dict[str, Any] = project.model_assignments or {}
    assignment = assignments.get(role)
    if not isinstance(assignment, dict) or not assignment.get("model"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"no model is assigned to the {role} role for this project."
                " Set it in the project's model assignments before starting a run."
            ),
        )

    name = str(assignment.get("provider", ""))
    try:
        registry.spec(name)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown provider {name!r}"
        ) from exc

    if not registry.is_configured(name):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{name} has no api key configured, so the {role} role cannot run",
        )

    return registry.build(name), str(assignment["model"])
