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

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sse_starlette.sse import EventSourceResponse

from econometrica.agents.data_steward import DataSteward
from econometrica.agents.narrator import Narrator
from econometrica.agents.orchestrator import TIERS, Orchestrator, Tier
from econometrica.agents.planner import Planner
from econometrica.agents.validator import Validator
from econometrica.api.deps import (
    PriceSourceDep,
    ProviderRegistryDep,
    SessionDep,
    get_chat_or_404,
    get_project_or_404,
)
from econometrica.db.models import Project
from econometrica.llm.base import LLMProvider
from econometrica.llm.registry import ProviderRegistry
from econometrica.schemas.run import RunStart

router = APIRouter(prefix="/api/chats", tags=["runs"])

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
) -> EventSourceResponse:
    """Run the full pipeline for one question, streaming progress as SSE."""
    chat = await get_chat_or_404(session, chat_id)
    project = await get_project_or_404(session, chat.project_id)

    orchestrator = _build(project, registry, source)

    async def events() -> AsyncIterator[dict[str, str]]:
        async for event in orchestrator.stream(payload.question, context=payload.context):
            # The name goes in the SSE `event:` field, as `messages.py` does,
            # so repeating it in the body would just be noise on the wire.
            yield {
                "event": event.name,
                "data": event.model_dump_json(exclude={"name"}),
            }

    return EventSourceResponse(events())


# --- internals --------------------------------------------------------------


def _build(
    project: Project, registry: ProviderRegistry, source: PriceSourceDep
) -> Orchestrator:
    planner_provider, planner_model = _bind("planner", project, registry)
    narrator_provider, narrator_model = _bind("narrator", project, registry)

    tier = _tier(project)
    validator = None
    if tier != "single" and "validator" in (project.model_assignments or {}):
        provider, model = _bind("validator", project, registry)
        validator = Validator(provider, model)

    return Orchestrator(
        planners=[Planner(planner_provider, planner_model)],
        steward=DataSteward(source),
        validator=validator,
        narrator=Narrator(narrator_provider, narrator_model),
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
