"""Phases 8-10 on top of chat: citations, rate-limit notice, silent retries, Stop / Retry / Cancel,
fault injection, conversation memory and deletion."""
import json
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.generation import inspector, memory
from app.generation.prompts import GENERIC_ERROR
from app.models import Chunk, Conversation, Message

JWT = "def validate_token(token):\n    return decode(token)\n"
PAY = "def charge_card(amount):\n    return invoice(amount)\n"
README = "# Demo\n\nThe validate_token helper checks every request.\n"


def _events(res):
    out = []
    for block in res.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def _project(client, ingest, items=(("jwt.py", JWT), ("pay.py", PAY))):
    pid = (await client.post("/projects", json={"name": "Demo"})).json()["id"]
    files = [("files", (p, d.encode())) for p, d in items]
    res = await client.post(f"/projects/{pid}/upload", files=files, data={"paths": [p for p, _ in items]})
    assert res.status_code == 200
    await ingest(pid)
    return pid


async def _ask(client, pid, text):
    return await client.post(f"/projects/{pid}/conversations", json={"content": text})


async def _messages(sessions):
    async with sessions() as s:
        return (await s.scalars(select(Message).order_by(Message.created_at, Message.id))).all()


# --- Phase 8: citations ---
async def test_sources_are_built_by_the_app_streamed_and_frozen_on_the_answer(client, ingest, sessions):
    pid = await _project(client, ingest, [("jwt.py", JWT), ("README.md", README), ("pay.py", PAY)])
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert [n for n, _ in events][-2:] == ["sources", "done"]
    sources = dict(events)["sources"]["sources"]
    by_path = {s["file_path"]: s for s in sources}
    assert by_path["jwt.py"]["ranges"] == [[1, 2]] and by_path["jwt.py"]["label"] == "jwt.py — Lines 1–2"
    if "README.md" in by_path:  # a non-code file shows its path only
        assert by_path["README.md"]["ranges"] == [] and by_path["README.md"]["label"] == "README.md"
    assert "pay.py" not in by_path
    saved = (await _messages(sessions))[-1]
    assert saved.role == "assistant" and saved.sources_json == sources  # frozen with the answer
    cid = events[0][1]["conversation_id"]
    restored = (await client.get(f"/conversations/{cid}")).json()["messages"][-1]
    assert restored["sources_json"] == sources


async def test_no_context_reply_has_no_sources(client, ingest):
    pid = await _project(client, ingest)
    names = [n for n, _ in _events(await _ask(client, pid, "best beaches in goa"))]
    assert names == ["conversation", "no_context"]


# --- LLM-phase behaviour ---
async def test_rate_limit_wait_is_announced_before_the_first_token(client, ingest, llm):
    pid = await _project(client, ingest)
    llm.wait_seconds = 42.4
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert [n for n, _ in events][:3] == ["conversation", "waiting", "token"]
    assert events[1][1] == {"seconds": 42}


async def test_failure_before_the_first_token_is_retried_silently(client, ingest, sessions, llm):
    pid = await _project(client, ingest)
    llm.fail_times = 1
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert "error" not in [n for n, _ in events] and events[-1][0] == "done"
    assert len(llm.calls) == 2
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "answered"), ("assistant", None)]


async def test_retries_are_limited_then_the_generic_error_appears(client, ingest, sessions, llm):
    pid = await _project(client, ingest)
    llm.fail_times = 99
    res = await _ask(client, pid, "validate_token decode token")
    assert _events(res)[-1] == ("error", {"message": GENERIC_ERROR})
    assert len(llm.calls) == settings.generation_retries + 1
    assert "exploded" not in res.text
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "failed")]


async def test_a_silent_stream_is_cut_off_by_the_inactivity_timeout(client, ingest, sessions, llm, monkeypatch):
    monkeypatch.setattr(settings, "llm_inactivity_seconds", 0.2)
    monkeypatch.setattr(settings, "generation_retries", 0)
    pid = await _project(client, ingest)
    llm.hang = True
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert events[-1] == ("error", {"message": GENERIC_ERROR})
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "failed")]


# --- Phase 10: fault injection (dev only) ---
async def test_fault_llm_once_succeeds_after_one_silent_retry(client, ingest, llm, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "fault_llm", "once")
    pid = await _project(client, ingest)
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert events[-1][0] == "done" and "error" not in [n for n, _ in events]
    assert len(llm.calls) == 1  # the injected failure happened before the provider was reached


async def test_fault_llm_always_ends_in_the_generic_error_and_keeps_the_question(client, ingest, sessions, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "fault_llm", "always")
    pid = await _project(client, ingest)
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert events[-1] == ("error", {"message": GENERIC_ERROR})
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "failed")]


async def test_fault_retrieval_gives_the_generic_error_without_calling_the_llm(client, ingest, llm, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", True)
    monkeypatch.setattr(settings, "fault_retrieval", "always")
    pid = await _project(client, ingest)
    assert _events(await _ask(client, pid, "validate_token decode token"))[-1] == ("error", {"message": GENERIC_ERROR})
    assert llm.calls == []


async def test_fault_flags_are_ignored_outside_dev_mode(client, ingest, monkeypatch):
    monkeypatch.setattr(settings, "dev_mode", False)
    monkeypatch.setattr(settings, "fault_llm", "always")
    pid = await _project(client, ingest)
    assert _events(await _ask(client, pid, "validate_token decode token"))[-1][0] == "done"


# --- Phase 10: Retry and Cancel ---
async def _failed_conversation(client, ingest, llm):
    pid = await _project(client, ingest)
    llm.fail_times = 99
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    llm.fail_times = 0
    detail = (await client.get(f"/conversations/{cid}")).json()
    return pid, cid, detail["messages"][0]["id"]


async def test_retry_reuses_the_same_user_message(client, ingest, sessions, llm):
    pid, cid, mid = await _failed_conversation(client, ingest, llm)
    events = _events(await client.post(f"/messages/{mid}/retry"))
    assert events[-1][0] == "done"
    saved = await _messages(sessions)
    assert [(m.role, m.gen_state) for m in saved] == [("user", "answered"), ("assistant", None)]  # no duplicate
    assert str(saved[0].id) == mid


async def test_retry_is_refused_for_an_answered_message(client, ingest, llm):
    pid = await _project(client, ingest)
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    mid = (await client.get(f"/conversations/{cid}")).json()["messages"][0]["id"]
    assert (await client.post(f"/messages/{mid}/retry")).status_code == 409


async def test_cancel_clears_the_stopped_state_and_keeps_the_question(client, ingest, sessions, llm):
    pid, cid, mid = await _failed_conversation(client, ingest, llm)
    assert (await client.post(f"/messages/{mid}/dismiss")).status_code == 204
    detail = (await client.get(f"/conversations/{cid}")).json()
    assert [(m["role"], m["gen_state"]) for m in detail["messages"]] == [("user", "dismissed")]
    assert (await client.post(f"/messages/{mid}/retry")).status_code == 409  # nothing left to retry


async def test_a_new_question_abandons_the_unanswered_one(client, ingest, llm):
    pid, cid, mid = await _failed_conversation(client, ingest, llm)
    res = await client.post(f"/conversations/{cid}/messages", json={"content": "charge_card invoice amount"})
    assert _events(res)[-1][0] == "done"
    assert (await client.post(f"/messages/{mid}/retry")).status_code == 409  # no longer the newest message
    assert [m["role"] for m in llm.calls[-1]] == ["system", "user"]  # the unanswered question is not history


# --- Phase 9: conversation management and memory ---
async def test_deleting_a_conversation_keeps_the_projects_files_and_chunks(client, ingest, sessions):
    pid = await _project(client, ingest)
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    async with sessions() as s:
        before = len((await s.scalars(select(Chunk))).all())
    assert (await client.delete(f"/conversations/{cid}")).status_code == 204
    assert (await client.get(f"/conversations/{cid}")).status_code == 404
    assert (await client.get(f"/projects/{pid}/conversations")).json() == []
    async with sessions() as s:
        assert len((await s.scalars(select(Chunk))).all()) == before
        assert (await s.scalars(select(Message))).all() == []
    assert len((await client.get(f"/projects/{pid}/files")).json()) == 2


async def test_long_conversation_is_summarized_in_the_background(client, ingest, sessions, llm, monkeypatch):
    monkeypatch.setattr(settings, "summary_threshold_tokens", 40)
    monkeypatch.setattr(settings, "summary_keep_messages", 2)
    monkeypatch.setattr(settings, "dev_mode", True)
    pid = await _project(client, ingest)
    llm.tokens = ["An answer that is long enough to count toward the summary threshold. " * 2]
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    await memory.settle()
    for q in ("validate_token decode token", "validate_token decode token"):
        await client.post(f"/conversations/{cid}/messages", json={"content": q})
        await memory.settle()
    async with sessions() as s:
        conv = await s.get(Conversation, uuid.UUID(cid))
        assert conv.summary and conv.summary_checkpoint_message_id is not None

    # the next prompt carries the summary and only the messages after the checkpoint
    llm.tokens = ["ok"]
    llm.calls.clear()
    await client.post(f"/conversations/{cid}/messages", json={"content": "validate_token decode token"})
    prompt = llm.calls[-1]
    assert "Conversation so far" in prompt[0]["content"]
    ctx = (await client.get(f"/conversations/{cid}/context")).json()
    assert ctx["summary"] and ctx["turn"]["summary_used"] is True
    assert ctx["turn"]["messages"][0]["role"] == "system"


async def test_follow_up_retrieval_borrows_the_previous_question(client, ingest, llm):
    pid = await _project(client, ingest)
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    await client.post(f"/conversations/{cid}/messages", json={"content": "decode that token"})
    turn = inspector.last(cid)
    assert turn["retrieval_query"] == "validate_token decode token\ndecode that token"
    assert "--- jwt.py" in turn["messages"][-1]["content"]


async def test_context_inspector_is_dev_only(client, ingest, monkeypatch):
    pid = await _project(client, ingest)
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    monkeypatch.setattr(settings, "dev_mode", False)
    assert (await client.get(f"/conversations/{cid}/context")).status_code == 404


# --- Stop while the model is still thinking (cancellation, not a clean close) ---
async def test_cancelling_a_turn_that_is_waiting_on_the_llm_marks_it_stopped(client, ingest, sessions, llm):
    import asyncio

    from app.generation import chat

    pid = await _project(client, ingest)
    async with sessions() as s:
        conv = Conversation(project_id=uuid.UUID(pid), title="t")
        s.add(conv)
        await s.flush()
        user = Message(conversation_id=conv.id, role="user", content="validate_token decode token", gen_state="pending")
        s.add(user)
        await s.commit()
    llm.hang = True  # the provider never produces a token: the turn is parked inside the LLM call

    async def consume():
        async for _ in chat.stream_answer(sessions, uuid.UUID(pid), conv.id, user.id, "validate_token decode token"):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.5)  # retrieval done, waiting for the first token
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "stopped")]
