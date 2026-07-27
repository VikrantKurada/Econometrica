"""The metrics endpoint.

Read-only and unfiltered: a single-user local workbench has one tenant, and a
metrics view that needed scoping would be answering a question nobody has.
"""

from fastapi import APIRouter

from econometrica.api.deps import SessionDep
from econometrica.telemetry.metrics import Metrics, collect_metrics

router = APIRouter(prefix="/api", tags=["telemetry"])


@router.get("/metrics", response_model=Metrics)
async def read_metrics(session: SessionDep) -> Metrics:
    return await collect_metrics(session)
