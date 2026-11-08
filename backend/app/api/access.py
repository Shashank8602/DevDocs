"""Admin endpoints for folder / file access rules, the By-person explanation, and the audit log.

Rules are plain data an admin typed in; nothing is seeded. A change applies to the very next request (no
re-index), and conversation summaries of the project are cleared so memory is rebuilt from what each person
can still read (see generation/memory.py)."""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.auth_deps import project_for, require_admin
from app.db import get_session
from app.ingestion import rules as path_rules
from app.models import AccessRule, AuditLog, Conversation, File, ProjectMember, User
from app.security import MIN_PASSWORD_LENGTH, hash_password

router = APIRouter(prefix="/projects/{project_id}", tags=["access"])


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(max_length=2000)] = ""  # "" = the whole project
    visibility: Literal["everyone", "people", "admin"]
    user_ids: list[uuid.UUID] = []


class RuleOut(BaseModel):
    path: str
    visibility: str
    user_ids: list[str]


class FileAccess(BaseModel):
    id: uuid.UUID
    path: str
    status: str


class AccessOut(BaseModel):
    rules: list[RuleOut]
    files: list[FileAccess]


class PersonFile(BaseModel):
    id: uuid.UUID
    path: str
    can_read: bool
    reason: str


class MemberIn(BaseModel):
    """Add a person to this project: an existing account by email, or a new one (then `password` is required)."""
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(max_length=254)]
    name: Annotated[str, Field(max_length=100)] = ""
    password: Annotated[str, Field(max_length=200)] | None = None


class MemberOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str


class AuditOut(BaseModel):
    id: uuid.UUID
    created_at: str
    user_email: str
    question: str
    outcome: str
    served_paths: list[str]


def _norm(path: str) -> str:
    if path.strip().strip("/") == "":
        return ""
    normalized = path_rules.normalize_path(path.strip().strip("/"))
    if normalized is None:
        raise HTTPException(422, "Invalid path")
    return normalized


def _out(rule: AccessRule) -> RuleOut:
    return RuleOut(path=rule.path, visibility=rule.visibility, user_ids=list(rule.user_ids or []))


async def _forget_what_was_visible(session: AsyncSession, project_id: uuid.UUID) -> None:
    """A conversation summary may contain text from files a person can no longer read: drop them all, and
    memory is rebuilt from the messages that are still allowed."""
    await session.execute(
        update(Conversation).where(Conversation.project_id == project_id).values(
            summary=None, summary_checkpoint_message_id=None, recent_token_estimate=0,
        )
    )


@router.get("/access", response_model=AccessOut)
async def get_access(
    project_id: uuid.UUID, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    await project_for(session, admin, project_id)
    rules = await access.load_rules(session, project_id)
    files = (await session.execute(
        select(File.id, File.file_path, File.status).where(File.project_id == project_id).order_by(File.file_path)
    )).all()
    return AccessOut(
        rules=[_out(r) for _, r in sorted(rules.items())],
        files=[FileAccess(id=i, path=p, status=s) for i, p, s in files],
    )


@router.put("/access", response_model=RuleOut)
async def put_rule(
    project_id: uuid.UUID, body: RuleIn, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, admin, project_id)
    path = _norm(body.path)
    user_ids: list[str] = []
    if body.visibility == "people":
        wanted = list(dict.fromkeys(body.user_ids))
        members = await access.member_ids(session, project_id)
        if any(str(u) not in members for u in wanted):  # rules can only name people who belong to this project
            raise HTTPException(422, "Everyone on a rule must be a member of this project")
        user_ids = [str(u) for u in wanted]
    rule = (await access.load_rules(session, project_id)).get(path)
    if rule is None:
        rule = AccessRule(project_id=project_id, path=path, visibility=body.visibility, user_ids=user_ids)
        session.add(rule)
    else:
        rule.visibility, rule.user_ids = body.visibility, user_ids
    await _forget_what_was_visible(session, project_id)
    await session.commit()
    return _out(rule)


@router.delete("/access", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rule(
    project_id: uuid.UUID, path: str = Query("", max_length=2000), admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Remove a rule: the path goes back to inheriting from its parent folder."""
    await project_for(session, admin, project_id)
    rule = (await access.load_rules(session, project_id)).get(_norm(path))
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such rule")
    await session.delete(rule)
    await _forget_what_was_visible(session, project_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/access/person/{user_id}", response_model=list[PersonFile])
async def person_view(
    project_id: uuid.UUID, user_id: uuid.UUID, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Every file with whether this person can read it and why."""
    await project_for(session, admin, project_id)
    person = await session.get(User, user_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    rules = await access.load_rules(session, project_id)
    files = (await session.execute(
        select(File.id, File.file_path).where(File.project_id == project_id).order_by(File.file_path)
    )).all()
    member = await access.is_member(session, person, project_id)
    out = []
    for file_id, path in files:
        allowed, why = access.explain(person, rules, path, member)
        out.append(PersonFile(id=file_id, path=path, can_read=allowed, reason=why))
    return out


def _member_out(user: User) -> MemberOut:
    return MemberOut(id=user.id, email=user.email, name=user.name, role=user.role)


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    project_id: uuid.UUID, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session),
):
    await project_for(session, admin, project_id)
    users = (await session.scalars(
        select(User).join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id).order_by(User.email)
    )).all()
    return [_member_out(u) for u in users]


@router.post("/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def add_member(
    project_id: uuid.UUID, body: MemberIn, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Adds a person to this project only. They see nothing in it until a rule shares something with them."""
    await project_for(session, admin, project_id)
    email = body.email.strip().lower()
    if "@" not in email or len(email) < 3:
        raise HTTPException(422, "Enter a valid email address.")
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        if not body.password or len(body.password) < MIN_PASSWORD_LENGTH:
            raise HTTPException(422, f"A new person needs a password of at least {MIN_PASSWORD_LENGTH} characters.")
        user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), role="member")
        session.add(user)
        await session.flush()
    elif user.role == "admin":
        raise HTTPException(status.HTTP_409_CONFLICT, "Admins already read every project.")
    session.add(ProjectMember(project_id=project_id, user_id=user.id))
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "That person is already in this project.") from exc
    return _member_out(user)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: uuid.UUID, user_id: uuid.UUID, admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Takes a person out of this project: off every rule, their chats here deleted, the project vanishes for them."""
    await project_for(session, admin, project_id)
    member = await session.scalar(
        select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
    )
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this project")
    for rule in (await access.load_rules(session, project_id)).values():
        if str(user_id) in (rule.user_ids or []):
            rule.user_ids = [u for u in rule.user_ids if u != str(user_id)]
    await session.execute(delete(Conversation).where(Conversation.project_id == project_id, Conversation.user_id == user_id))
    await session.delete(member)
    await _forget_what_was_visible(session, project_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(
    project_id: uuid.UUID, limit: int = Query(200, ge=1, le=1000), admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Who asked what, and which files were served for it. Admin only."""
    await project_for(session, admin, project_id)
    rows = (await session.scalars(
        select(AuditLog).where(AuditLog.project_id == project_id).order_by(AuditLog.created_at.desc()).limit(limit)
    )).all()
    return [
        AuditOut(
            id=r.id, created_at=r.created_at.isoformat(), user_email=r.user_email, question=r.question,
            outcome=r.outcome, served_paths=list(r.served_paths or []),
        )
        for r in rows
    ]
