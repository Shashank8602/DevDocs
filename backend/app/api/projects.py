import logging
import shutil
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.files import project_dir
from app.db import get_session
from app.models import File, Project
from app.providers.embeddings import get_embedding_provider
from app.schemas import LanguageCount, ProjectCreate, ProjectOut, ProjectOverview, ProjectUpdate
from app.search import indexer

router = APIRouter(prefix="/projects", tags=["projects"])
log = logging.getLogger("devdocs.projects")


async def _get_or_404(session: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def _out(session: AsyncSession, project: Project) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    out.file_count = await session.scalar(select(func.count()).where(File.project_id == project.id)) or 0
    return out


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, session: AsyncSession = Depends(get_session)):
    project = Project(name=body.name, description=body.description or None)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return await _out(session, project)


@router.get("", response_model=list[ProjectOut])
async def list_projects(session: AsyncSession = Depends(get_session)):
    result = await session.scalars(select(Project).order_by(Project.updated_at.desc()))
    return [await _out(session, p) for p in result.all()]


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return await _out(session, await _get_or_404(session, project_id))


@router.get("/{project_id}/overview", response_model=ProjectOverview)
async def project_overview(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await _get_or_404(session, project_id)
    by_type = dict((await session.execute(
        select(File.file_type, func.count()).where(File.project_id == project_id).group_by(File.file_type)
    )).all())
    langs = (await session.execute(
        select(File.language, func.count()).where(File.project_id == project_id, File.language.is_not(None))
        .group_by(File.language).order_by(func.count().desc(), File.language)
    )).all()
    last = await session.scalar(select(func.max(File.updated_at)).where(File.project_id == project_id))
    return ProjectOverview(
        file_count=sum(by_type.values()),
        by_type={t: by_type.get(t, 0) for t in ("doc", "source", "other")},
        languages=[LanguageCount(language=lang, count=n) for lang, n in langs],
        last_updated=last,
    )


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID, body: ProjectUpdate, session: AsyncSession = Depends(get_session)
):
    project = await _get_or_404(session, project_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "name cannot be empty")
    for field, value in changes.items():
        setattr(project, field, (value or None) if field == "description" else value)
    await session.commit()
    await session.refresh(project)
    return await _out(session, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    # Dependent rows cascade in Postgres; the project's ES docs are removed afterwards.
    project = await _get_or_404(session, project_id)
    fingerprint = project.embedding_fingerprint or get_embedding_provider().fingerprint
    await session.delete(project)
    await session.commit()
    try:
        await indexer.delete_project_docs(fingerprint, project_id)
    except Exception as exc:  # `reconcile` removes whatever is left; retrieval never reads it meanwhile
        log.warning("could not remove ES docs of project %s: %r", project_id, exc)
    shutil.rmtree(project_dir(project_id), ignore_errors=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
