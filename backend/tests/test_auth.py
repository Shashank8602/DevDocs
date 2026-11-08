"""Phase A: identity. First-run admin setup, email + password sign-in, session cookie, people management,
and that nothing is reachable without signing in."""
import time
import uuid

import pytest

from app.api import auth as auth_api
from app.config import settings
from app.security import SESSION_COOKIE, hash_password, make_token, read_token, verify_password
from tests.conftest import make_user

PASSWORD = "correct-horse-battery"


# --- passwords and tokens (pure) ---
def test_password_hash_is_salted_and_verifies():
    a, b = hash_password(PASSWORD), hash_password(PASSWORD)
    assert a != b and a.startswith("scrypt$") and PASSWORD not in a
    assert verify_password(PASSWORD, a) and not verify_password("wrong", a)
    assert not verify_password(PASSWORD, "garbage") and not verify_password(PASSWORD, "scrypt$1$2$3$x$y")


def test_session_token_round_trip_expiry_and_tampering():
    uid = uuid.uuid4()
    token = make_token(uid)
    assert read_token(token) == uid
    assert read_token(make_token(uid, now=time.time() - 30 * 24 * 3600)) is None  # expired
    user_part, expiry, signature = token.split(".")
    assert read_token(f"{uuid.uuid4()}.{expiry}.{signature}") is None  # signed for someone else
    assert read_token(f"{user_part}.{int(expiry) + 9999}.{signature}") is None  # expiry extended
    assert read_token(None) is None and read_token("") is None and read_token("a.b") is None and read_token("a.b.c") is None


# --- first run ---
async def test_first_run_creates_the_admin_once(http):
    c = http()
    assert (await c.get("/auth/status")).json() == {"needs_setup": True, "local_mode": False}
    res = await c.post("/auth/setup", json={"email": "Owner@Example.test", "password": PASSWORD, "name": "Owner"})
    assert res.status_code == 201
    assert res.json()["email"] == "owner@example.test" and res.json()["role"] == "admin"
    assert SESSION_COOKIE in res.cookies and "httponly" in res.headers["set-cookie"].lower()
    assert (await c.get("/auth/status")).json()["needs_setup"] is False
    again = await c.post("/auth/setup", json={"email": "other@example.test", "password": PASSWORD})
    assert again.status_code == 409  # nobody can create a second admin through the open endpoint


async def test_setup_validates_email_and_password(http):
    c = http()
    assert (await c.post("/auth/setup", json={"email": "not-an-email", "password": PASSWORD})).status_code == 422
    assert (await c.post("/auth/setup", json={"email": "a@b.test", "password": "short"})).status_code == 422
    assert (await c.get("/auth/status")).json()["needs_setup"] is True  # nothing was created


async def test_setup_hands_pre_existing_chats_to_the_new_admin(http, sessions):
    from app.models import Conversation, Project, User
    from sqlalchemy import select

    async with sessions() as s:
        p = Project(name="Old")
        s.add(p)
        await s.flush()
        s.add(Conversation(project_id=p.id, title="from before sign-in"))
        await s.commit()
    await http().post("/auth/setup", json={"email": "owner@example.test", "password": PASSWORD})
    async with sessions() as s:
        conv = (await s.scalars(select(Conversation))).one()
        owner = (await s.scalars(select(User))).one()
        assert conv.user_id == owner.id


# --- sign in / out ---
async def test_login_logout_and_me_with_the_cookie(http, sessions):
    await make_user(sessions, "ana@example.test", password=PASSWORD)
    c = http()
    assert (await c.get("/auth/me")).status_code == 401
    res = await c.post("/auth/login", json={"email": " ANA@example.test ", "password": PASSWORD})
    assert res.status_code == 200
    c.cookies.set(SESSION_COOKIE, res.cookies[SESSION_COOKIE])
    me = (await c.get("/auth/me")).json()
    assert me["email"] == "ana@example.test" and me["role"] == "member" and "password_hash" not in me
    assert (await c.get("/projects")).status_code == 200
    out = await c.post("/auth/logout")
    assert out.status_code == 204 and SESSION_COOKIE in out.headers["set-cookie"]
    c.cookies.clear()
    assert (await c.get("/projects")).status_code == 401


async def test_wrong_password_and_unknown_email_give_the_same_answer(http, sessions):
    await make_user(sessions, "ana@example.test", password=PASSWORD)
    c = http()
    wrong = await c.post("/auth/login", json={"email": "ana@example.test", "password": "nope-nope-nope"})
    unknown = await c.post("/auth/login", json={"email": "ghost@example.test", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()


async def test_repeated_wrong_passwords_lock_the_address_for_a_while(http, sessions):
    await make_user(sessions, "ana@example.test", password=PASSWORD)
    c = http()
    for _ in range(auth_api.MAX_FAILURES):
        assert (await c.post("/auth/login", json={"email": "ana@example.test", "password": "bad-password"})).status_code == 401
    locked = await c.post("/auth/login", json={"email": "ana@example.test", "password": PASSWORD})
    assert locked.status_code == 429  # even the right password waits
    auth_api._failures["ana@example.test"] = (auth_api.MAX_FAILURES, time.time() - 1)  # the lock runs out
    assert (await c.post("/auth/login", json={"email": "ana@example.test", "password": PASSWORD})).status_code == 200


async def test_a_forged_or_expired_cookie_is_rejected_and_a_deleted_user_loses_the_session(http, sessions, admin):
    ana = await make_user(sessions, "ana@example.test")
    c = http()
    c.cookies.set(SESSION_COOKIE, "not-a-token")
    assert (await c.get("/projects")).status_code == 401
    c.cookies.set(SESSION_COOKIE, make_token(ana.id, now=time.time() - 30 * 24 * 3600))
    assert (await c.get("/projects")).status_code == 401
    ana_client = http(ana)
    assert (await ana_client.get("/projects")).status_code == 200
    assert (await http(admin).delete(f"/users/{ana.id}")).status_code == 204
    assert (await ana_client.get("/projects")).status_code == 401  # the token is still signed, the person is gone


# --- nothing without signing in ---
async def test_every_data_endpoint_requires_sign_in(http, admin):
    pid, fid = uuid.uuid4(), uuid.uuid4()
    anon = http()
    calls = [
        anon.get("/projects"), anon.post("/projects", json={"name": "x"}), anon.get(f"/projects/{pid}"),
        anon.get(f"/projects/{pid}/overview"), anon.patch(f"/projects/{pid}", json={"name": "y"}),
        anon.delete(f"/projects/{pid}"), anon.get(f"/projects/{pid}/files"), anon.get(f"/files/{fid}/content"),
        anon.delete(f"/files/{fid}"), anon.get(f"/projects/{pid}/conversations"), anon.get(f"/conversations/{fid}"),
        anon.post(f"/projects/{pid}/conversations", json={"content": "hi"}),
        anon.post(f"/conversations/{fid}/messages", json={"content": "hi"}),
        anon.get("/settings"), anon.get("/users"), anon.get(f"/projects/{pid}/access"),
        anon.get(f"/projects/{pid}/audit"), anon.get("/ingestion/active"), anon.get("/upload-rules"),
        anon.get(f"/projects/{pid}/es-inspector"), anon.get("/auth/me"),
    ]
    for call in calls:
        res = await call
        assert res.status_code in (401, 404), res.request.url  # dev routes may answer 404 first; never 200
        if res.status_code == 404:
            assert "/es-inspector" in str(res.request.url)
    # the only public routes
    assert (await anon.get("/health")).status_code == 200
    assert (await anon.get("/auth/status")).status_code == 200


# --- LOCAL_MODE ---
async def test_local_mode_acts_as_the_first_admin_without_credentials(http, admin, monkeypatch):
    anon = http()
    assert (await anon.get("/projects")).status_code == 401
    monkeypatch.setattr(settings, "local_mode", True)
    assert (await anon.get("/auth/me")).json()["email"] == admin.email
    bad = http()
    bad.cookies.set(SESSION_COOKIE, "garbage")  # a bad credential is not the same as none
    assert (await bad.get("/auth/me")).status_code == 401


# --- people ---
async def test_admin_creates_edits_and_deletes_people(http, admin):
    ac = http(admin)
    made = await ac.post("/users", json={"email": "New@Example.test", "password": PASSWORD, "name": "New"})
    assert made.status_code == 201 and made.json()["role"] == "member" and made.json()["email"] == "new@example.test"
    assert (await ac.post("/users", json={"email": "new@example.test", "password": PASSWORD})).status_code == 409
    uid = made.json()["id"]

    assert (await ac.patch(f"/users/{uid}", json={"name": "Renamed", "role": "admin"})).json()["role"] == "admin"
    assert (await ac.patch(f"/users/{uid}", json={"password": "short"})).status_code == 422
    assert (await ac.patch(f"/users/{uid}", json={"password": "a-new-password"})).status_code == 200
    login = await http().post("/auth/login", json={"email": "new@example.test", "password": "a-new-password"})
    assert login.status_code == 200
    assert (await ac.get("/users")).json()[-1]["email"] == "new@example.test"
    assert (await ac.delete(f"/users/{uid}")).status_code == 204
    assert (await ac.delete(f"/users/{uid}")).status_code == 404


async def test_the_last_admin_cannot_be_removed_or_demoted(http, admin):
    ac = http(admin)
    assert (await ac.delete(f"/users/{admin.id}")).status_code == 409  # not yourself
    assert (await ac.patch(f"/users/{admin.id}", json={"role": "member"})).status_code == 409
    assert (await ac.get("/auth/me")).json()["role"] == "admin"


async def test_passwords_are_never_returned(http, admin, sessions):
    await make_user(sessions, "ana@example.test")
    text = (await http(admin).get("/users")).text
    assert "password" not in text and "scrypt" not in text
