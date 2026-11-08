"""Sign-in and people management. Email + password, a signed HttpOnly session cookie, a first-run screen that
creates the initial admin. The admin creates everyone else. No groups: access is granted to people (access.py)."""
import re
import time
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_deps import current_user, require_admin
from app.config import settings
from app.db import get_session
from app.models import AccessRule, Conversation, User
from app.security import (
    DUMMY_HASH, MIN_PASSWORD_LENGTH, SESSION_COOKIE, SESSION_SECONDS, hash_password, make_token, verify_password,
)

router = APIRouter(tags=["auth"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BAD_LOGIN = "Wrong email or password."

# Brute-force guard: after MAX_FAILURES wrong passwords for an address, further attempts are refused for LOCK_SECONDS.
MAX_FAILURES, LOCK_SECONDS = 5, 60
_failures: dict[str, tuple[int, float]] = {}


def reset_throttle() -> None:
    _failures.clear()


def _normalize_email(raw: str) -> str:
    email = raw.strip().lower()
    if len(email) > 254 or not _EMAIL.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    return email


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            422, f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )
    if len(password) > 200:
        raise HTTPException(422, "Password is too long.")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str


class Me(UserOut):
    previewing: bool = False


class Credentials(BaseModel):
    email: Annotated[str, Field(max_length=254)]
    password: Annotated[str, Field(max_length=200)]


class SetupBody(Credentials):
    name: Annotated[str, Field(max_length=100)] = ""


class UserCreate(SetupBody):
    role: Literal["admin", "member"] = "member"


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(max_length=100)] | None = None
    role: Literal["admin", "member"] | None = None
    password: Annotated[str, Field(max_length=200)] | None = None


def _set_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        SESSION_COOKIE, make_token(user.id), max_age=SESSION_SECONDS, httponly=True, samesite="lax",
        secure=settings.cookie_secure, path="/",
    )


@router.get("/auth/status")
async def auth_status(session: AsyncSession = Depends(get_session)):
    """Public: what the sign-in screen needs to decide between 'create the admin' and 'sign in'."""
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    return {"needs_setup": total == 0, "local_mode": settings.local_mode}


@router.post("/auth/setup", response_model=Me, status_code=status.HTTP_201_CREATED)
async def setup(body: SetupBody, response: Response, session: AsyncSession = Depends(get_session)):
    """First run only: creates the admin and signs them in. Refused once any user exists."""
    email = _normalize_email(body.email)
    _check_password(body.password)
    await session.execute(text("SELECT pg_advisory_xact_lock(7001)"))  # two simultaneous setups cannot both win
    if (await session.scalar(select(func.count()).select_from(User))) or 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Setup is already done.")
    admin = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), role="admin")
    session.add(admin)
    await session.flush()
    # chats made before sign-in existed belong to whoever sets the app up
    await session.execute(update(Conversation).where(Conversation.user_id.is_(None)).values(user_id=admin.id))
    await session.commit()
    _set_cookie(response, admin)
    return Me.model_validate(admin)


@router.post("/auth/login", response_model=Me)
async def login(body: Credentials, response: Response, session: AsyncSession = Depends(get_session)):
    email = body.email.strip().lower()
    count, locked_until = _failures.get(email, (0, 0.0))
    now = time.time()
    if count >= MAX_FAILURES and now < locked_until:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")
    if count >= MAX_FAILURES:  # the lock has run out: start counting again
        count = 0
    user = await session.scalar(select(User).where(User.email == email))
    ok = verify_password(body.password, user.password_hash if user else DUMMY_HASH)
    if user is None or not ok:
        if len(_failures) > 10_000:  # unknown addresses must not grow this without bound
            _failures.clear()
        _failures[email] = (count + 1, now + LOCK_SECONDS)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)
    _failures.pop(email, None)
    _set_cookie(response, user)
    return Me.model_validate(user)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout():
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/auth/me", response_model=Me)
async def me(user: User = Depends(current_user)):
    out = Me.model_validate(user)
    out.previewing = bool(getattr(user, "previewing", False))
    return out


@router.get("/users", response_model=list[UserOut])
async def list_users(_: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    return (await session.scalars(select(User).order_by(User.created_at, User.email))).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, _: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    email = _normalize_email(body.email)
    _check_password(body.password)
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password), role=body.role)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Someone with that email already exists.") from exc
    return user


async def _admin_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserPatch, _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if body.role is not None and body.role != user.role:
        if user.role == "admin" and await _admin_count(session) <= 1:
            raise HTTPException(status.HTTP_409_CONFLICT, "There must be at least one admin.")
        user.role = body.role
    if body.name is not None:
        user.name = body.name.strip()
    if body.password is not None:
        _check_password(body.password)
        user.password_hash = hash_password(body.password)
    await session.commit()
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, admin: User = Depends(require_admin), session: AsyncSession = Depends(get_session)):
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete yourself.")
    if user.role == "admin" and await _admin_count(session) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "There must be at least one admin.")
    # take the person off every rule that named them; their conversations go with them (FK cascade)
    for rule in (await session.scalars(select(AccessRule))).all():
        if str(user.id) in (rule.user_ids or []):
            rule.user_ids = [u for u in rule.user_ids if u != str(user.id)]
    await session.delete(user)
    await session.commit()
