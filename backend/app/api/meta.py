from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import File, Project
from app.schemas import ActiveIngestion

router = APIRouter(tags=["meta"])


@router.get("/config")
async def get_config():
    return {"dev_mode": settings.dev_mode}


@router.get("/ingestion/active", response_model=list[ActiveIngestion])
async def active_ingestion(session: AsyncSession = Depends(get_session)):
    """Projects that are ingesting right now; feeds the global ingestion indicator."""
    remaining = func.count(case((File.status.in_(("pending", "processing")), 1)))
    rows = await session.execute(
        select(Project.id, Project.name, remaining, func.count(File.id))
        .join(File, File.project_id == Project.id)
        .where(Project.status == "processing")
        .group_by(Project.id, Project.name)
        .order_by(Project.name)
    )
    return [ActiveIngestion(project_id=i, project_name=n, remaining=r, total=t) for i, n, r, t in rows.all()]
