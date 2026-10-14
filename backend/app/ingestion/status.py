"""Project status is derived from its file statuses; it is never set independently. The one exception is
`needs_reindex`, set when the embedding settings change and cleared only by a completed re-index."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, Project


def derive_status(counts: dict[str, int]) -> str:
    if not counts:
        return "empty"
    if counts.get("pending") or counts.get("processing"):
        return "processing"
    return "ready" if counts.get("ready") else "failed"


async def refresh_project_status(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Recompute and store the project status. The caller commits."""
    project = await session.get(Project, project_id)
    if project is None:
        return
    rows = await session.execute(
        select(File.status, func.count()).where(File.project_id == project_id).group_by(File.status)
    )
    new = derive_status(dict(rows.all()))
    if project.status == "needs_reindex" and new != "empty":
        return  # only a completed re-index clears it (embedding settings changed since these files were embedded)
    if project.status == "processing" and new in ("ready", "failed"):
        project.last_ingested_at = datetime.now(timezone.utc)
    project.status = new
