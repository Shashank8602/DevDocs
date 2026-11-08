"""FastAPI dependencies that answer "who is asking, and may they see this?". Keeping the lookup here, in one
place, means a different sign-in method (OIDC, SSO) would replace `current_user` and nothing else.

Credentials: the session cookie, or an `Authorization: Bearer <token>` header (used by tests and scripts).
`X-Preview-As: <user id>` lets an admin see the app exactly as that member would (read-only): the returned
user *is* the member, so every permission check downstream behaves as it would for them."""
import uuid

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access
from app.config import settings
from app.db import get_session
from app.models import File, Project, User
from app.security import SESSION_COOKIE, read_token

NOT_SIGNED_IN = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")


def _token_of(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
    x_preview_as: str | None = Header(None),
) -> User:
    user_id = read_token(_token_of(request))
    user = await session.get(User, user_id) if user_id else None
    if user is None and settings.local_mode and _token_of(request) is None:
        user = await session.scalar(select(User).where(User.role == "admin").order_by(User.created_at).limit(1))
    if user is None:
        raise NOT_SIGNED_IN
    user.previewing = False  # plain attribute, not a column
    if x_preview_as and user.is_admin:
        try:
            target = await session.get(User, uuid.UUID(x_preview_as))
        except ValueError:
            target = None
        if target is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        target.previewing = True
        return target
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


async def not_previewing(user: User = Depends(current_user)) -> User:
    """For actions that create data in the user's name: a preview is read-only."""
    if getattr(user, "previewing", False):
        raise HTTPException(status.HTTP_409_CONFLICT, "Preview mode is read-only")
    return user


async def dev_admin_only(user: User = Depends(current_user)) -> User:
    """Dev tools look like a missing page to everyone but an admin."""
    if not user.is_admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


async def project_for(session: AsyncSession, user: User, project_id: uuid.UUID) -> Project:
    """The project, or 404 when it does not exist or the user can read nothing in it (same answer for both)."""
    project = await session.get(Project, project_id)
    if project is None or not (user.is_admin or await access.can_read_any(session, user, project_id)):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


async def file_for(session: AsyncSession, user: User, file_id: uuid.UUID) -> File:
    """The file, or 404 when it does not exist or the user may not read it (same answer for both)."""
    row = await session.get(File, file_id)
    if row is None or not await access.can_read_file(session, user, row):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return row
