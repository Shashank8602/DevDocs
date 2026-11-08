import logging
import mimetypes
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File as FormFile, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.auth_deps import current_user, dev_admin_only, file_for, project_for, require_admin
from app.config import settings
from app.db import get_session
from app.ingestion import rules, safety, worker
from app.ingestion.change_detection import Diff, diff
from app.ingestion.hashing import copy_and_hash
from app.ingestion.status import refresh_project_status
from app.models import Chunk, File, Project, User
from app.providers.embeddings import get_embedding_provider
from app.search import indexer
from app.schemas import ChunkOut, FileOut, FileReplaceResult, UploadReport, UploadResultItem

router = APIRouter(tags=["files"])
log = logging.getLogger("devdocs.files")


def project_dir(project_id: uuid.UUID) -> Path:
    return settings.data_dir / "projects" / str(project_id)


def disk_path(project_id: uuid.UUID, file_path: str) -> Path:
    return project_dir(project_id) / file_path


async def remove_es_docs(project: Project | None, file_id: uuid.UUID) -> None:
    """Delete a file's ES docs after its Postgres row is gone. A failure is logged, not raised:
    retrieval intersects with Postgres READY ids, so stray docs can't leak, and `reconcile` removes them."""
    fingerprint = (project.embedding_fingerprint if project else None) or get_embedding_provider().fingerprint
    try:
        await indexer.delete_file_docs(fingerprint, file_id)
    except Exception as exc:
        log.warning("could not remove ES docs of file %s: %r", file_id, exc)


@router.get("/upload-rules")
async def get_upload_rules(_: User = Depends(current_user)):
    return rules.rules_payload()


def _stage(upload_file: UploadFile, staging: Path, n: int) -> tuple[Path, str, int]:
    """Write the upload to a staging file, hashing as it streams. Returns (path, sha256, size)."""
    staged = staging / str(n)
    with staged.open("wb") as out:
        digest, size = copy_and_hash(upload_file.file, out)
    return staged, digest, size


def _content_problem(path: str, staged: Path) -> tuple[str, str | None] | None:
    """(reason, detail) when the staged file must be skipped for what is inside it: a credential (it would be
    sent to the embedding and chat providers) or generated / minified code (it only burns embedding quota)."""
    file_type = rules.classify(path).file_type
    data = staged.read_bytes()[: safety.SCAN_BYTES + 4096] if staged.stat().st_size <= safety.SCAN_BYTES else _head_and_tail(staged)
    secret = safety.find_secret(path, data, file_type)
    if secret:
        return "possible_secret", f"looks like {secret}"
    if safety.looks_generated(data, file_type):
        return "generated_file", None
    return None


def _head_and_tail(staged: Path) -> bytes:
    """For a big file: the first SCAN_BYTES, plus the last 2 KB (where a source-map footer sits)."""
    with staged.open("rb") as fh:
        head = fh.read(safety.SCAN_BYTES)
        fh.seek(-2048, 2)
        return head + fh.read()


def _place(staged: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), target)


def _changes(stored_hashes: dict[str, str | None], incoming_hashes: dict[str, str]) -> Diff:
    if incoming_hashes:
        return diff(stored_hashes, incoming_hashes)
    # Nothing usable was uploaded (everything filtered out): never treat that as "delete the project".
    return Diff(unchanged=sorted(stored_hashes))


@router.post("/projects/{project_id}/upload", response_model=UploadReport)
async def upload(
    project_id: uuid.UUID,
    files: list[UploadFile] = FormFile(...),
    paths: list[str] = Form(...),
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Upload or re-upload a whole folder. The upload is compared with the stored files:
    new paths are created, changed content is modified, paths absent from the upload are deleted."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if project.status == "processing":
        raise HTTPException(status.HTTP_409_CONFLICT, "Project is being processed")
    if project.status == "needs_reindex":
        raise HTTPException(status.HTTP_409_CONFLICT, "Re-index required before adding files")
    if len(files) != len(paths):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "files and paths must match")

    staging = project_dir(project_id) / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        results: list[UploadResultItem] = []
        incoming: dict[str, tuple[Path, str, int]] = {}  # path -> (staged file, hash, size)
        total = 0
        for n, (upload_file, raw_path) in enumerate(zip(files, paths)):
            path = rules.normalize_path(raw_path)
            if path is None:
                results.append(UploadResultItem(path=raw_path, status="rejected", reason="invalid_path"))
                continue
            size = upload_file.size or 0
            reason = rules.rejection_reason(path, size)
            if reason is None and total + size > rules.MAX_TOTAL_BYTES:
                reason = "total_limit_exceeded"
            if reason:
                results.append(UploadResultItem(path=path, status="rejected", reason=reason))
                continue
            staged = _stage(upload_file, staging, n)
            skipped = _content_problem(path, staged[0])
            if skipped:
                staged[0].unlink(missing_ok=True)
                results.append(UploadResultItem(path=path, status="rejected", reason=skipped[0], detail=skipped[1]))
                continue
            incoming[path] = staged
            total += size
            results.append(UploadResultItem(path=path, status="accepted"))

        stored = {f.file_path: f for f in (await session.scalars(select(File).where(File.project_id == project_id))).all()}
        changes = _changes({p: f.file_hash for p, f in stored.items()}, {p: v[1] for p, v in incoming.items()})

        deleted_ids: list[uuid.UUID] = []
        for path in changes.deleted:
            disk_path(project_id, path).unlink(missing_ok=True)
            await session.delete(stored[path])
            deleted_ids.append(stored[path].id)
        for path in changes.modified:
            staged, digest, size = incoming[path]
            _place(staged, disk_path(project_id, path))
            row = stored[path]
            info = rules.classify(path)
            row.file_hash, row.file_size, row.status, row.error_message = digest, size, "pending", None
            row.file_type, row.language = info.file_type, info.language
        for path in changes.created:
            staged, digest, size = incoming[path]
            _place(staged, disk_path(project_id, path))
            info = rules.classify(path)
            session.add(File(
                project_id=project_id, file_path=path, file_name=path.rsplit("/", 1)[-1],
                file_type=info.file_type, language=info.language, file_size=size,
                file_hash=digest, status="pending",
            ))
        await refresh_project_status(session, project_id)
        await session.commit()
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    for file_id in deleted_ids:
        await remove_es_docs(project, file_id)
    if changes.created or changes.modified:
        worker.enqueue(project_id)
    accepted = len(incoming)
    return UploadReport(
        accepted=accepted, rejected=len(results) - accepted, results=results,
        created=changes.created, modified=changes.modified, deleted=changes.deleted, unchanged=changes.unchanged,
    )


@router.get("/projects/{project_id}/files", response_model=list[FileOut])
async def list_files(
    project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Only the files this user may read: a restricted file is not listed at all."""
    await project_for(session, user, project_id)
    allowed = await access.readable_file_ids(session, user, project_id)
    result = await session.scalars(
        select(File).where(File.project_id == project_id, File.id.in_(allowed)).order_by(File.file_path)
    )
    return result.all()


async def _file_or_404(session: AsyncSession, file_id: uuid.UUID) -> File:
    """Admin-only callers (replace, delete, retry): any existing file."""
    row = await session.get(File, file_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row


@router.get("/files/{file_id}/content")
async def file_content(
    file_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    row = await file_for(session, user, file_id)  # 404 for a missing file and for one the user may not read
    path = disk_path(row.project_id, row.file_path)
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    media_type = mimetypes.guess_type(row.file_name)[0] or "application/octet-stream"
    if media_type.startswith("text/html"):
        media_type = "text/plain"  # HTML is shown as source, never rendered
    return FileResponse(path, media_type=media_type, content_disposition_type="inline")


@router.put("/files/{file_id}", response_model=FileReplaceResult)
async def replace_file(
    file_id: uuid.UUID, file: UploadFile = FormFile(...), _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Replace one file's content in place (same path). Identical content is skipped."""
    row = await _file_or_404(session, file_id)
    project = await session.get(Project, row.project_id)
    if project is not None and project.status == "processing":
        raise HTTPException(status.HTTP_409_CONFLICT, "Project is being processed")
    if project is not None and project.status == "needs_reindex":
        raise HTTPException(status.HTTP_409_CONFLICT, "Re-index required before changing files")

    staging = project_dir(row.project_id) / f".staging-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        staged, digest, size = _stage(file, staging, 0)
        if size > rules.MAX_FILE_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is too large")
        problem = _content_problem(row.file_path, staged)
        if problem:  # same checks as an upload: the file is not accepted and the stored version is untouched
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"This file was not accepted ({problem[0]}).")
        if digest == row.file_hash:
            return FileReplaceResult(id=row.id, changed=False)
        _place(staged, disk_path(row.project_id, row.file_path))
        row.file_hash, row.file_size, row.status, row.error_message = digest, size, "pending", None
        await refresh_project_status(session, row.project_id)
        await session.commit()
        worker.enqueue(row.project_id)
        return FileReplaceResult(id=row.id, changed=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_file(
    file_id: uuid.UUID, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    row = await _file_or_404(session, file_id)
    project_id = row.project_id
    disk_path(project_id, row.file_path).unlink(missing_ok=True)
    await session.delete(row)
    await session.flush()
    await refresh_project_status(session, project_id)
    project = await session.get(Project, project_id)
    await session.commit()
    await remove_es_docs(project, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/files/{file_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_file(
    file_id: uuid.UUID, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    """Queue a failed file for processing again."""
    row = await _file_or_404(session, file_id)
    if row.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed files can be retried")
    project = await session.get(Project, row.project_id)
    if project is not None and project.status == "needs_reindex":
        raise HTTPException(status.HTTP_409_CONFLICT, "Re-index required before retrying files")
    row.status, row.error_message = "pending", None
    await refresh_project_status(session, row.project_id)
    await session.commit()
    worker.enqueue(row.project_id)
    return {"id": row.id}


@router.get("/projects/{project_id}/files/{file_id}/chunks", response_model=list[ChunkOut])
async def file_chunks(
    project_id: uuid.UUID, file_id: uuid.UUID, _: User = Depends(dev_admin_only),
    session: AsyncSession = Depends(get_session),
):
    """Chunk Inspector data. Dev-only: does not exist unless DEV_MODE=true."""
    if not settings.dev_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    row = await _file_or_404(session, file_id)
    if row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    result = await session.scalars(select(Chunk).where(Chunk.file_id == file_id).order_by(Chunk.ordinal))
    return result.all()
