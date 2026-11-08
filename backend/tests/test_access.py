"""Phase B/D: permission-aware access. The leak suite: every user, file and rule below is created inside the test
(nothing is seeded by the app). Each test asserts that a person who may not read a file cannot reach it
through any surface: retrieval, chat, citations, file content, lists, counts, memory, dev tools."""
import json
import uuid

import pytest
from sqlalchemy import delete, select, update

from app import access
from app.config import settings
from app.generation import memory
from app.generation.prompts import NO_CONTEXT_REPLY
from app.models import AccessRule, AuditLog, Conversation, File, Message, User
from app.retrieval import pipeline
from tests.conftest import add_member, make_user

SENTENCE = "def validate_token(token):\n    return decode(token)\n"  # the same text in two files
SALARY = "def salary_bands(level):\n    return lookup(level)\n"  # exists only in the restricted file
PAY = "def charge_card(amount):\n    return invoice(amount)\n"

ITEMS = [
    ("handbook/auth.py", SENTENCE),
    ("hr/auth_copy.py", SENTENCE),
    ("hr/salary.py", SALARY),
    ("handbook/pay.py", PAY),
]


def _events(res) -> list[tuple[str, dict]]:
    out = []
    for block in res.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def _rule(client, project_id, path, visibility, user_ids=()):
    res = await client.put(
        f"/projects/{project_id}/access",
        json={"path": path, "visibility": visibility, "user_ids": [str(u) for u in user_ids]},
    )
    assert res.status_code == 200, res.text
    return res.json()


class World:
    """An admin, two members and a project with a public folder and a restricted one."""

    def __init__(self, http, admin, ana, ben, pid, ids):
        self.http, self.admin, self.ana, self.ben, self.pid, self.ids = http, admin, ana, ben, pid, ids
        self.ac, self.ana_c, self.ben_c = http(admin), http(ana), http(ben)

    async def rule(self, path, visibility, user_ids=()):
        return await _rule(self.ac, self.pid, path, visibility, user_ids)


@pytest.fixture
async def world(http, admin, sessions, ingest):
    ana = await make_user(sessions, "ana@example.test")
    ben = await make_user(sessions, "ben@example.test")
    ac = http(admin)
    pid = (await ac.post("/projects", json={"name": "Shared"})).json()["id"]
    res = await ac.post(
        f"/projects/{pid}/upload", files=[("files", (p, d.encode())) for p, d in ITEMS],
        data={"paths": [p for p, _ in ITEMS]},
    )
    assert res.status_code == 200
    await ingest(pid)
    files = (await ac.get(f"/projects/{pid}/files")).json()
    w = World(http, admin, ana, ben, pid, {f["file_path"]: f["id"] for f in files})
    await add_member(ac, pid, ana)
    await add_member(ac, pid, ben)
    await w.rule("handbook", "everyone")
    await w.rule("hr", "people", [ana.id])  # Ana reads hr; Ben does not
    return w


async def _trace(sessions, user, pid, q):
    async with sessions() as s:
        return await pipeline.run(s, uuid.UUID(pid), q, viewer=user)


async def _ask(client, pid, text):
    return await client.post(f"/projects/{pid}/conversations", json={"content": text})


# --- the resolver (pure) ---
def _rule_obj(path, visibility, users=()):
    return AccessRule(project_id=uuid.uuid4(), path=path, visibility=visibility, user_ids=[str(u) for u in users])


def test_most_specific_rule_wins_and_no_rule_means_admin_only():
    ana = User(id=uuid.uuid4(), email="a@x", role="member")
    rules = {r.path: r for r in [
        _rule_obj("docs", "everyone"), _rule_obj("docs/secret", "admin"), _rule_obj("docs/secret/ok.md", "people", [ana.id]),
    ]}
    assert access.rule_allows(ana, access.governing_rule(rules, "docs/readme.md"))
    assert not access.rule_allows(ana, access.governing_rule(rules, "docs/secret/x.md"))
    assert access.rule_allows(ana, access.governing_rule(rules, "docs/secret/ok.md"))  # file overrides its folder
    assert not access.rule_allows(ana, access.governing_rule(rules, "other/file.md"))  # no rule: admin only
    admin = User(id=uuid.uuid4(), email="b@x", role="admin")
    assert access.rule_allows(admin, access.governing_rule(rules, "other/file.md"))


def test_folder_rule_does_not_match_a_sibling_with_the_same_prefix():
    ana = User(id=uuid.uuid4(), email="a@x", role="member")
    rules = {"docs": _rule_obj("docs", "everyone")}
    assert access.governing_rule(rules, "docs-private/a.md") is None  # "docs-private" is not inside "docs"


def test_a_root_rule_covers_everything_that_has_no_closer_rule():
    ana = User(id=uuid.uuid4(), email="a@x", role="member")
    rules = {"": _rule_obj("", "everyone"), "hr": _rule_obj("hr", "admin")}
    assert access.rule_allows(ana, access.governing_rule(rules, "any/file.md"))
    assert not access.rule_allows(ana, access.governing_rule(rules, "hr/pay.md"))


# --- retrieval ---
async def test_restricted_chunks_never_enter_a_members_retrieval(world, sessions):
    ben_trace = await _trace(sessions, world.ben, world.pid, "validate_token decode token")
    restricted = {world.ids["hr/auth_copy.py"], world.ids["hr/salary.py"]}
    seen = {h.file_id for h in ben_trace.bm25 + ben_trace.vector} | {c.file_id for c in ben_trace.candidates + ben_trace.final}
    assert seen and not (seen & restricted)  # not in either search leg, not a candidate, not served
    assert {c.file_path for c in ben_trace.final} == {"handbook/auth.py"}

    ana_trace = await _trace(sessions, world.ana, world.pid, "validate_token decode token")
    assert {c.file_path for c in ana_trace.final} == {"handbook/auth.py", "hr/auth_copy.py"}


async def test_a_restricted_file_cannot_crowd_out_a_permitted_one(world, sessions):
    """Top-K comes only from permitted files: the permitted copy is returned even though a forbidden copy exists."""
    trace = await _trace(sessions, world.ben, world.pid, "validate_token decode token")
    assert trace.final and trace.final[0].file_path == "handbook/auth.py"


async def test_chat_refuses_identically_when_only_restricted_files_could_answer(world, llm):
    asked = _events(await _ask(world.ben_c, world.pid, "salary_bands lookup level"))
    off_topic = _events(await _ask(world.ben_c, world.pid, "best beaches in goa"))
    assert [n for n, _ in asked] == ["conversation", "no_context"] == [n for n, _ in off_topic]
    assert asked[1][1]["content"] == off_topic[1][1]["content"] == NO_CONTEXT_REPLY
    assert "permission" not in asked[1][1]["content"].lower()
    assert llm.calls == []  # the model was never given anything

    allowed = _events(await _ask(world.ana_c, world.pid, "salary_bands lookup level"))
    assert [n for n, _ in allowed][-2:] == ["sources", "done"]
    assert "hr/salary.py" in {s["file_path"] for s in dict(allowed)["sources"]["sources"]}


async def test_the_prompt_never_contains_text_from_a_file_the_user_cannot_read(world, llm):
    await _ask(world.ben_c, world.pid, "validate_token decode token")
    (prompt,) = llm.calls
    text = "\n".join(m["content"] for m in prompt)
    assert "handbook/auth.py" in text and "hr/" not in text and "salary" not in text


# --- every other surface ---
async def test_a_member_with_no_rule_sees_nothing_and_the_project_does_not_exist_for_them(http, admin, sessions, ingest):
    carl = await make_user(sessions, "carl@example.test")
    ac, cc = http(admin), http(carl)
    pid = (await ac.post("/projects", json={"name": "Private"})).json()["id"]
    await ac.post(f"/projects/{pid}/upload", files=[("files", ("a.py", SENTENCE.encode()))], data={"paths": ["a.py"]})
    await ingest(pid)
    assert (await cc.get("/projects")).json() == []
    assert (await cc.get(f"/projects/{pid}")).status_code == 404
    assert (await cc.get(f"/projects/{pid}/files")).status_code == 404
    assert (await cc.get(f"/projects/{pid}/overview")).status_code == 404
    assert (await _ask(cc, pid, "validate_token")).status_code == 404
    # indistinguishable from a project that does not exist
    assert (await cc.get(f"/projects/{uuid.uuid4()}")).status_code == 404


async def test_file_lists_and_counts_leave_out_restricted_files(world):
    ben_files = (await world.ben_c.get(f"/projects/{world.pid}/files")).json()
    assert {f["file_path"] for f in ben_files} == {"handbook/auth.py", "handbook/pay.py"}
    ana_files = (await world.ana_c.get(f"/projects/{world.pid}/files")).json()
    assert len(ana_files) == 4
    assert (await world.ben_c.get(f"/projects/{world.pid}")).json()["file_count"] == 2
    assert (await world.ben_c.get(f"/projects/{world.pid}/overview")).json()["file_count"] == 2
    assert (await world.ac.get(f"/projects/{world.pid}/overview")).json()["file_count"] == 4
    listed = (await world.ben_c.get("/projects")).json()
    assert [p["file_count"] for p in listed] == [2]


async def test_file_content_is_404_for_a_restricted_file_exactly_like_a_missing_one(world):
    restricted = await world.ben_c.get(f"/files/{world.ids['hr/salary.py']}/content")
    missing = await world.ben_c.get(f"/files/{uuid.uuid4()}/content")
    assert restricted.status_code == missing.status_code == 404
    assert restricted.json() == missing.json()
    assert (await world.ben_c.get(f"/files/{world.ids['handbook/pay.py']}/content")).status_code == 200
    assert (await world.ana_c.get(f"/files/{world.ids['hr/salary.py']}/content")).status_code == 200


async def test_only_admins_can_change_files_projects_and_settings(world):
    fid = world.ids["handbook/pay.py"]
    for call in (
        world.ana_c.post("/projects", json={"name": "x"}),
        world.ana_c.patch(f"/projects/{world.pid}", json={"name": "y"}),
        world.ana_c.delete(f"/projects/{world.pid}"),
        world.ana_c.delete(f"/files/{fid}"),
        world.ana_c.post(f"/files/{fid}/retry"),
        world.ana_c.put(f"/files/{fid}", files={"file": ("pay.py", b"x")}),
        world.ana_c.post(f"/projects/{world.pid}/upload", files=[("files", ("n.py", b"x"))], data={"paths": ["n.py"]}),
        world.ana_c.get("/settings"),
        world.ana_c.put("/settings", json={}),
        world.ana_c.post("/settings/test", json={"target": "llm"}),
        world.ana_c.get("/users"),
        world.ana_c.post("/users", json={"email": "z@example.test", "password": "password-123"}),
        world.ana_c.get(f"/projects/{world.pid}/access"),
        world.ana_c.put(f"/projects/{world.pid}/access", json={"path": "", "visibility": "everyone"}),
        world.ana_c.delete(f"/projects/{world.pid}/access?path=hr"),
        world.ana_c.get(f"/projects/{world.pid}/audit"),
        world.ana_c.get(f"/projects/{world.pid}/access/person/{world.ben.id}"),
    ):
        assert (await call).status_code == 403
    assert (await world.ac.get(f"/projects/{world.pid}")).status_code == 200  # nothing was deleted


async def test_dev_tools_look_like_missing_pages_to_members(world, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", True)
    pid, fid = world.pid, world.ids["handbook/pay.py"]
    for url in (f"/projects/{pid}/es-inspector", f"/projects/{pid}/es-count",
                f"/projects/{pid}/retrieval-playground?q=x", f"/projects/{pid}/retrieval-pipeline?q=x",
                f"/projects/{pid}/files/{fid}/vectors", f"/projects/{pid}/files/{fid}/chunks"):
        assert (await world.ana_c.get(url)).status_code == 404, url
    assert (await world.ac.get(f"/projects/{pid}/es-inspector")).status_code == 200


async def test_the_ingestion_indicator_shows_nothing_to_members(world):
    assert (await world.ben_c.get("/ingestion/active")).json() == []


# --- changes apply immediately, with no re-index ---
async def test_granting_and_revoking_applies_to_the_very_next_question(world, llm):
    assert [n for n, _ in _events(await _ask(world.ben_c, world.pid, "salary_bands lookup level"))][-1] == "no_context"

    await world.rule("hr", "people", [world.ana.id, world.ben.id])  # no re-index in between
    granted = _events(await _ask(world.ben_c, world.pid, "salary_bands lookup level"))
    assert [n for n, _ in granted][-2:] == ["sources", "done"]
    cid = granted[0][1]["conversation_id"]

    await world.rule("hr", "people", [world.ana.id])  # take Ben off again
    assert [n for n, _ in _events(await _ask(world.ben_c, world.pid, "salary_bands lookup level"))][-1] == "no_context"

    # the old citation in Ben's earlier chat no longer opens or names the file
    detail = (await world.ben_c.get(f"/conversations/{cid}")).json()
    cited = detail["messages"][-1]["sources_json"]
    assert cited == [{"file_id": None, "file_path": "", "file_type": None, "ranges": [],
                      "label": "Source no longer available", "available": False}]
    assert "salary" not in json.dumps(cited)


async def test_everyone_rule_and_removing_a_rule_fall_back_to_the_parent(world):
    await world.rule("hr/salary.py", "everyone")  # a file overrides its folder
    ben_files = {f["file_path"] for f in (await world.ben_c.get(f"/projects/{world.pid}/files")).json()}
    assert ben_files == {"handbook/auth.py", "handbook/pay.py", "hr/salary.py"}
    assert (await world.ac.delete(f"/projects/{world.pid}/access?path=hr/salary.py")).status_code == 204
    ben_files = {f["file_path"] for f in (await world.ben_c.get(f"/projects/{world.pid}/files")).json()}
    assert "hr/salary.py" not in ben_files


async def _upload_more(world, ingest, path, data):
    """An upload is a whole folder (absent paths are deleted), so send the existing files again plus the new one."""
    current = await world.ac.get(f"/projects/{world.pid}/files")
    items = [(f["file_path"], (await world.ac.get(f"/files/{f['id']}/content")).content) for f in current.json()]
    items.append((path, data))
    res = await world.ac.post(
        f"/projects/{world.pid}/upload", files=[("files", (p, d)) for p, d in items], data={"paths": [p for p, _ in items]},
    )
    assert res.status_code == 200
    await ingest(world.pid)


async def test_uploading_a_new_file_into_a_restricted_area_does_not_expose_it(world, ingest):
    await _upload_more(world, ingest, "hr/new.py", b"def newest():\n    return 1\n")
    names = {f["file_path"] for f in (await world.ben_c.get(f"/projects/{world.pid}/files")).json()}
    assert "hr/new.py" not in names
    await _upload_more(world, ingest, "loose/x.py", b"def loose():\n    return 1\n")  # no rule at all: admin only
    names = {f["file_path"] for f in (await world.ben_c.get(f"/projects/{world.pid}/files")).json()}
    assert "loose/x.py" not in names


# --- memory ---
async def test_a_rule_change_clears_conversation_summaries(world, sessions):
    cid = _events(await _ask(world.ana_c, world.pid, "salary_bands lookup level"))[0][1]["conversation_id"]
    async with sessions() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        conv.summary, conv.recent_token_estimate = "Ana asked about salary_bands.", 500
        await s.commit()
    await world.rule("hr", "people", [world.ana.id, world.ben.id])
    async with sessions() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv.summary is None and conv.summary_checkpoint_message_id is None and conv.recent_token_estimate == 0


async def test_history_drops_answers_that_cite_files_no_longer_readable(world, sessions):
    """Memory is rebuilt from what the person can still see: the question/answer about a lost file is left out."""
    await world.rule("hr", "people", [world.ana.id, world.ben.id])
    cid = _events(await _ask(world.ben_c, world.pid, "salary_bands lookup level"))[0][1]["conversation_id"]
    await world.ben_c.post(f"/conversations/{cid}/messages", json={"content": "charge_card invoice amount"})
    await world.rule("hr", "people", [world.ana.id])  # Ben loses hr
    async with sessions() as s:
        summary, history = await memory.load_memory(s, uuid.UUID(cid))
    assert summary is None
    flat = " ".join(c for _, c in history)
    assert "charge_card" in flat and "salary_bands" not in flat


def test_drop_unreadable_removes_the_question_with_its_answer():
    def msg(role, sources=None):
        return Message(id=uuid.uuid4(), conversation_id=uuid.uuid4(), role=role, content=role, sources_json=sources)

    q1, a1, q2, a2 = msg("user"), msg("assistant", [{"file_id": "f1"}]), msg("user"), msg("assistant", [{"file_id": "f2"}])
    assert memory.drop_unreadable([q1, a1, q2, a2], {"f2"}) == [q2, a2]
    assert memory.drop_unreadable([q1, a1, q2, a2], None) == [q1, a1, q2, a2]


# --- conversations belong to their owner ---
async def test_conversations_are_private_even_from_admins(world):
    cid = _events(await _ask(world.ana_c, world.pid, "validate_token decode token"))[0][1]["conversation_id"]
    for other in (world.ben_c, world.ac):
        assert (await other.get(f"/conversations/{cid}")).status_code == 404
        assert (await other.post(f"/conversations/{cid}/messages", json={"content": "hi"})).status_code == 404
        assert (await other.delete(f"/conversations/{cid}")).status_code == 404
        assert (await other.get(f"/projects/{world.pid}/conversations")).json() == []
    assert len((await world.ana_c.get(f"/projects/{world.pid}/conversations")).json()) == 1
    assert (await world.ana_c.get(f"/conversations/{cid}")).status_code == 200


async def test_retry_and_cancel_of_someone_elses_message_is_404(world, sessions):
    cid = _events(await _ask(world.ana_c, world.pid, "validate_token decode token"))[0][1]["conversation_id"]
    async with sessions() as s:
        m = (await s.scalars(select(Message).where(Message.role == "user"))).first()
        await s.execute(delete(Message).where(Message.role == "assistant"))  # the question is the newest message
        await s.execute(update(Message).where(Message.id == m.id).values(gen_state="failed"))
        await s.commit()
    assert (await world.ben_c.post(f"/messages/{m.id}/retry")).status_code == 404
    assert (await world.ben_c.post(f"/messages/{m.id}/dismiss")).status_code == 404
    assert (await world.ana_c.post(f"/messages/{m.id}/dismiss")).status_code == 204


# --- audit log ---
async def test_audit_log_records_who_asked_what_and_which_files_were_served(world):
    await _ask(world.ben_c, world.pid, "validate_token decode token")
    await _ask(world.ben_c, world.pid, "salary_bands lookup level")
    await _ask(world.ana_c, world.pid, "salary_bands lookup level")
    rows = (await world.ac.get(f"/projects/{world.pid}/audit")).json()
    by_user = {(r["user_email"], r["question"]): r for r in rows}
    ben_ok = by_user[("ben@example.test", "validate_token decode token")]
    assert ben_ok["outcome"] == "served" and ben_ok["served_paths"] == ["handbook/auth.py"]
    ben_refused = by_user[("ben@example.test", "salary_bands lookup level")]
    assert ben_refused["outcome"] == "refused" and ben_refused["served_paths"] == []
    ana = by_user[("ana@example.test", "salary_bands lookup level")]
    assert ana["outcome"] == "served" and "hr/salary.py" in ana["served_paths"]
    assert (await world.ben_c.get(f"/projects/{world.pid}/audit")).status_code == 403


# --- Access page data ---
async def test_person_view_explains_why_each_file_is_or_is_not_readable(world):
    rows = (await world.ac.get(f"/projects/{world.pid}/access/person/{world.ben.id}")).json()
    by_path = {r["path"]: r for r in rows}
    assert by_path["handbook/pay.py"]["can_read"] and "Everyone" in by_path["handbook/pay.py"]["reason"]
    assert not by_path["hr/salary.py"]["can_read"] and "Not named" in by_path["hr/salary.py"]["reason"]


async def test_rule_validation(world, http, sessions):
    bad = await world.ac.put(f"/projects/{world.pid}/access", json={"path": "../x", "visibility": "everyone"})
    assert bad.status_code == 422
    unknown = await world.ac.put(
        f"/projects/{world.pid}/access", json={"path": "hr", "visibility": "people", "user_ids": [str(uuid.uuid4())]},
    )
    assert unknown.status_code == 422
    assert (await world.ac.put(f"/projects/{world.pid}/access", json={"path": "hr", "visibility": "bogus"})).status_code == 422
    assert (await world.ac.delete(f"/projects/{world.pid}/access?path=nothing/here")).status_code == 404
    got = (await world.ac.get(f"/projects/{world.pid}/access")).json()
    assert {r["path"]: r["visibility"] for r in got["rules"]} == {"handbook": "everyone", "hr": "people"}
    assert len(got["files"]) == 4


async def test_deleting_a_person_removes_them_from_rules_and_drops_their_chats(world, sessions):
    _events(await _ask(world.ana_c, world.pid, "validate_token decode token"))
    assert (await world.ac.delete(f"/users/{world.ana.id}")).status_code == 204
    got = (await world.ac.get(f"/projects/{world.pid}/access")).json()
    assert all(str(world.ana.id) not in r["user_ids"] for r in got["rules"])
    async with sessions() as s:
        assert (await s.scalars(select(Conversation))).all() == []
        assert len((await s.scalars(select(AuditLog))).all()) == 1  # the audit log keeps its history


# --- preview as ---
async def test_preview_as_shows_exactly_what_that_member_sees_and_is_read_only(world):
    headers = {"X-Preview-As": str(world.ben.id)}
    files = (await world.ac.get(f"/projects/{world.pid}/files", headers=headers)).json()
    assert {f["file_path"] for f in files} == {"handbook/auth.py", "handbook/pay.py"}
    me = (await world.ac.get("/auth/me", headers=headers)).json()
    assert me["email"] == "ben@example.test" and me["previewing"] is True
    assert (await world.ac.get("/users", headers=headers)).status_code == 403  # admin pages vanish in preview
    assert (await _ask_with(world.ac, world.pid, "validate_token", headers)).status_code == 409  # read-only
    assert (await world.ac.get(f"/projects/{world.pid}/files")).status_code == 200


async def _ask_with(client, pid, text, headers):
    return await client.post(f"/projects/{pid}/conversations", json={"content": text}, headers=headers)


async def test_a_member_cannot_use_preview_to_become_someone_else(world):
    res = await world.ben_c.get(f"/projects/{world.pid}/files", headers={"X-Preview-As": str(world.ana.id)})
    assert {f["file_path"] for f in res.json()} == {"handbook/auth.py", "handbook/pay.py"}  # header ignored
    me = (await world.ben_c.get("/auth/me", headers={"X-Preview-As": str(world.ana.id)})).json()
    assert me["email"] == "ben@example.test" and me["previewing"] is False


# --- an admin reads everything ---
async def test_admin_reads_every_file_and_retrieval_includes_them(world, sessions):
    trace = await _trace(sessions, world.admin, world.pid, "salary_bands lookup level")
    assert "hr/salary.py" in {c.file_path for c in trace.final}
    assert (await world.ac.get(f"/files/{world.ids['hr/salary.py']}/content")).status_code == 200
    assert len((await world.ac.get(f"/projects/{world.pid}/files")).json()) == 4


# --- per-project members ---
async def test_membership_is_per_project_and_shares_nothing_by_itself(http, admin, sessions, ingest):
    dana = await make_user(sessions, "dana@example.test")
    ac, dc = http(admin), http(dana)
    a = (await ac.post("/projects", json={"name": "A"})).json()["id"]
    b = (await ac.post("/projects", json={"name": "B"})).json()["id"]
    for pid in (a, b):
        await ac.post(f"/projects/{pid}/upload", files=[("files", ("n.py", SENTENCE.encode()))], data={"paths": ["n.py"]})
        await ingest(pid)
        await _rule(ac, pid, "", "everyone")  # shared with every member of that project
    assert (await dc.get("/projects")).json() == []  # not a member anywhere: both projects are invisible
    assert (await dc.get(f"/projects/{a}/files")).status_code == 404

    await add_member(ac, a, dana)
    assert [p["id"] for p in (await dc.get("/projects")).json()] == [a]  # only project A
    assert (await dc.get(f"/projects/{a}/files")).status_code == 200
    assert (await dc.get(f"/projects/{b}")).status_code == 404 and (await dc.get(f"/projects/{b}/files")).status_code == 404
    assert (await _ask(dc, b, "validate_token")).status_code == 404

    # a member of a project with no rule shares nothing
    c = (await ac.post("/projects", json={"name": "C"})).json()["id"]
    await ac.post(f"/projects/{c}/upload", files=[("files", ("n.py", SENTENCE.encode()))], data={"paths": ["n.py"]})
    await add_member(ac, c, dana)
    assert (await dc.get(f"/projects/{c}")).status_code == 404  # member, but nothing shared: still invisible


async def test_a_rule_can_only_name_members_of_that_project(world, sessions):
    outsider = await make_user(sessions, "out@example.test")
    res = await world.ac.put(
        f"/projects/{world.pid}/access", json={"path": "hr", "visibility": "people", "user_ids": [str(outsider.id)]},
    )
    assert res.status_code == 422


async def test_everyone_means_every_member_of_the_project_not_every_account(world, http, sessions):
    outsider = await make_user(sessions, "out@example.test")
    await world.rule("hr", "everyone")
    assert (await http(outsider).get(f"/projects/{world.pid}/files")).status_code == 404
    assert len((await world.ben_c.get(f"/projects/{world.pid}/files")).json()) == 4  # a member now sees hr too


async def test_adding_a_person_creates_the_account_when_needed(world, http):
    new = await world.ac.post(
        f"/projects/{world.pid}/members", json={"email": "Fresh@Example.test", "name": "Fresh", "password": "password-123"},
    )
    assert new.status_code == 201 and new.json()["email"] == "fresh@example.test" and new.json()["role"] == "member"
    assert (await world.ac.post(f"/projects/{world.pid}/members", json={"email": "fresh@example.test"})).status_code == 409
    assert (await world.ac.post(f"/projects/{world.pid}/members", json={"email": "nobody@example.test"})).status_code == 422
    assert (await world.ac.post(f"/projects/{world.pid}/members", json={"email": world.admin.email})).status_code == 409
    login = await http().post("/auth/login", json={"email": "fresh@example.test", "password": "password-123"})
    assert login.status_code == 200
    listed = {m["email"] for m in (await world.ac.get(f"/projects/{world.pid}/members")).json()}
    assert listed == {"ana@example.test", "ben@example.test", "fresh@example.test"}
    assert (await world.ana_c.get(f"/projects/{world.pid}/members")).status_code == 403
    assert (await world.ana_c.post(f"/projects/{world.pid}/members", json={"email": "x@example.test"})).status_code == 403


async def test_removing_a_member_ends_their_access_rules_and_chats_in_this_project(world, sessions):
    _events(await _ask(world.ana_c, world.pid, "validate_token decode token"))
    assert (await world.ac.delete(f"/projects/{world.pid}/members/{world.ana.id}")).status_code == 204
    assert (await world.ana_c.get(f"/projects/{world.pid}")).status_code == 404
    assert (await world.ana_c.get(f"/projects/{world.pid}/conversations")).status_code == 404
    rules = {r["path"]: r for r in (await world.ac.get(f"/projects/{world.pid}/access")).json()["rules"]}
    assert str(world.ana.id) not in rules["hr"]["user_ids"]
    async with sessions() as s:
        assert (await s.scalars(select(Conversation))).all() == []
    assert (await world.ac.delete(f"/projects/{world.pid}/members/{world.ana.id}")).status_code == 404
    assert (await world.ana_c.get("/auth/me")).status_code == 200  # the account itself is untouched


async def test_person_view_says_when_someone_is_not_a_member(world, sessions):
    outsider = await make_user(sessions, "out@example.test")
    rows = (await world.ac.get(f"/projects/{world.pid}/access/person/{outsider.id}")).json()
    assert rows and all(not r["can_read"] and r["reason"] == "Not a member of this project" for r in rows)


# --- select and ask ---
async def test_select_and_ask_pins_the_lines_the_server_reads_and_rechecks_access(world, llm, sessions):
    sel = {"file_id": world.ids["hr/salary.py"], "line_start": 1, "line_end": 2}
    # Ben may not read hr/salary.py: 404, exactly like a missing file, and nothing is created
    res = await world.ben_c.post(f"/projects/{world.pid}/conversations", json={"content": "explain this", "selection": sel})
    assert res.status_code == 404 and llm.calls == []
    async with sessions() as s:
        assert (await s.scalars(select(Conversation))).all() == []
    # an id from another project is not accepted either
    other = (await world.ac.post("/projects", json={"name": "Other"})).json()["id"]
    assert (await world.ac.post(f"/projects/{other}/conversations", json={"content": "x", "selection": sel})).status_code in (404, 409)

    # Ana may: the model is given the selected lines even though the question alone would be refused
    ok = _events(await world.ana_c.post(
        f"/projects/{world.pid}/conversations", json={"content": "why is this here?", "selection": sel},
    ))
    assert [n for n, _ in ok][-2:] == ["sources", "done"]
    prompt = "\n".join(m["content"] for m in llm.calls[-1])
    assert "salary_bands" in prompt and "hr/salary.py" in prompt
    cited = dict(ok)["sources"]["sources"]
    assert cited[0]["file_path"] == "hr/salary.py" and cited[0]["ranges"] == [[1, 2]]
    detail = (await world.ana_c.get(f"/conversations/{ok[0][1]['conversation_id']}")).json()
    assert "Selected: hr/salary.py, lines 1-2" in detail["messages"][0]["content"]


async def test_selection_prompt_has_its_own_block_so_this_has_an_antecedent(world, llm):
    sel = {"file_id": world.ids["hr/salary.py"], "line_start": 1, "line_end": 2}
    _events(await world.ana_c.post(f"/projects/{world.pid}/conversations", json={"content": "Explain this", "selection": sel}))
    user_msg = llm.calls[-1][-1]["content"]
    assert user_msg.startswith("The user selected these lines of hr/salary.py, lines 1-2")
    assert user_msg.index("salary_bands") < user_msg.index("Question:")


async def test_ask_about_lines_answers_in_the_stream_and_saves_nothing(world, llm, sessions):
    fid = world.ids["hr/salary.py"]
    body = {"content": "Explain this", "line_start": 1, "line_end": 2}
    # Ben may not read it: 404 like a missing file, and the model is never called
    assert (await world.ben_c.post(f"/files/{fid}/ask", json=body)).status_code == 404 and llm.calls == []
    # Ana may: the selected lines reach the model and the answer streams, but no conversation or message exists
    res = await world.ana_c.post(f"/files/{fid}/ask", json=body)
    events = _events(res)
    assert [n for n, _ in events][-2:] == ["sources", "done"]
    assert "".join(d["text"] for n, d in events if n == "token") == "Authentication uses JWT tokens."
    assert "salary_bands" in llm.calls[-1][-1]["content"]
    assert dict(events)["sources"]["sources"][0]["file_path"] == "hr/salary.py"
    async with sessions() as s:
        assert (await s.scalars(select(Conversation))).all() == []
        assert (await s.scalars(select(Message))).all() == []
    assert (await world.ana_c.post(f"/files/{fid}/ask", json={**body, "line_start": 0})).status_code == 422
    assert (await world.ana_c.post(f"/files/{fid}/ask", json={**body, "content": "  "})).status_code == 422


async def test_select_and_ask_rejects_bad_ranges_and_preview(world):
    fid = world.ids["handbook/pay.py"]
    assert (await world.ana_c.post(f"/projects/{world.pid}/conversations", json={
        "content": "x", "selection": {"file_id": fid, "line_start": 0, "line_end": 1}})).status_code == 422
    ok = await world.ana_c.post(f"/projects/{world.pid}/conversations", json={
        "content": "x", "selection": {"file_id": fid, "line_start": 1, "line_end": 9999}})
    assert ok.status_code == 200  # an over-long range is clamped to the file, not an error
