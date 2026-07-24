from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from econometrica.api.deps import SessionDep, get_project_or_404
from econometrica.db.models import Project
from econometrica.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(payload: ProjectCreate, session: SessionDep) -> ProjectRead:
    project = Project(**payload.model_dump())
    session.add(project)
    await session.commit()
    return ProjectRead.model_validate(project)


@router.get("")
async def list_projects(session: SessionDep) -> list[ProjectRead]:
    # created_at is the transaction timestamp, so rows created together tie;
    # name breaks the tie to keep the ordering stable for the UI.
    projects = await session.scalars(select(Project).order_by(Project.created_at, Project.name))
    return [ProjectRead.model_validate(project) for project in projects]


@router.get("/{project_id}")
async def get_project(project_id: UUID, session: SessionDep) -> ProjectRead:
    return ProjectRead.model_validate(await get_project_or_404(session, project_id))


@router.patch("/{project_id}")
async def update_project(
    project_id: UUID, payload: ProjectUpdate, session: SessionDep
) -> ProjectRead:
    project = await get_project_or_404(session, project_id)
    # exclude_unset is what makes this a PATCH rather than a PUT: a field the
    # client left out keeps its stored value instead of being reset.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await session.commit()
    return ProjectRead.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: UUID, session: SessionDep) -> None:
    project = await get_project_or_404(session, project_id)
    await session.delete(project)
    await session.commit()
