"""Phase 7: chat. Conversation creation on the first message, validation, the SSE stream, the no-context
fallback that skips the LLM, failure states, restore from the database, the LLM provider wrapper (mock
transport, rate limiter) and the title / context-builder helpers. Fake LLM and embeddings: no network."""
import json
import re
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.generation import chat
from app.generation.context_builder import build_messages, recent_history
from app.generation.prompts import GENERIC_ERROR, NO_CONTEXT_REPLY, SYSTEM_PROMPT, title_from_question
from app.models import Conversation, Message
from app.providers.llm import LLMError, OpenAICompatLLMProvider, get_llm_provider, set_llm_provider
from app.providers.rate_limit import RateLimiter, get_limiter, reset_limiters
from app.retrieval.selection import Candidate

JWT = "def validate_token(token):\n    return decode(token)\n"
PAY = "def charge_card(amount):\n    return invoice(amount)\n"


# --- helpers ---
async def _project(client, name="Demo"):
    return (await client.post("/projects", json={"name": name})).json()["id"]


async def _ready_project(client, ingest, name="Demo", items=(("jwt.py", JWT), ("pay.py", PAY))):
    pid = await _project(client, name)
    files = [("files", (p, d.encode())) for p, d in items]
    res = await client.post(f"/projects/{pid}/upload", files=files, data={"paths": [p for p, _ in items]})
    assert res.status_code == 200
    await ingest(pid)
    return pid


def _events(res) -> list[tuple[str, dict]]:
    out = []
    for block in res.text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


async def _ask(client, pid, text):
    return await client.post(f"/projects/{pid}/conversations", json={"content": text})


async def _count(sessions, model):
    async with sessions() as s:
        return await s.scalar(select(func.count()).select_from(model))


async def _messages(sessions):
    async with sessions() as s:
        return (await s.scalars(select(Message).order_by(Message.created_at, Message.id))).all()


# --- the happy path ---
async def test_first_message_creates_the_conversation_and_streams_a_grounded_answer(client, ingest, sessions, llm):
    pid = await _ready_project(client, ingest)
    res = await _ask(client, pid, "validate_token decode token?")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/event-stream")
    events = _events(res)
    assert [n for n, _ in events] == ["conversation", "token", "token", "token", "sources", "done"]
    assert events[0][1]["title"] == "Validate_token decode token"
    assert "".join(d["text"] for n, d in events if n == "token") == "Authentication uses JWT tokens."

    # the prompt: system prompt first, retrieved project context labelled with path and lines, then the question
    (messages,) = llm.calls
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "--- jwt.py, lines 1-2 ---" in messages[-1]["content"] and "validate_token" in messages[-1]["content"]
    assert "pay.py" not in messages[-1]["content"]  # not relevant: filtered out
    assert messages[-1]["content"].endswith("Question:\nvalidate_token decode token?")

    saved = await _messages(sessions)
    assert [(m.role, m.gen_state) for m in saved] == [("user", "answered"), ("assistant", None)]
    assert saved[1].content == "Authentication uses JWT tokens." and str(saved[1].id) == events[-1][1]["message_id"]


async def test_conversation_is_restored_from_the_database_without_an_llm_call(client, ingest, llm):
    pid = await _ready_project(client, ingest)
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    assert len(llm.calls) == 1
    detail = (await client.get(f"/conversations/{cid}")).json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "validate_token decode token"), ("assistant", "Authentication uses JWT tokens."),
    ]
    listed = (await client.get(f"/projects/{pid}/conversations")).json()
    assert [c["id"] for c in listed] == [cid]
    assert len(llm.calls) == 1  # opening a conversation never calls the LLM


async def test_follow_up_message_sends_recent_history_and_fresh_context(client, ingest, llm):
    pid = await _ready_project(client, ingest)
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    res = await client.post(f"/conversations/{cid}/messages", json={"content": "charge_card invoice amount"})
    assert [n for n, _ in _events(res)][:2] == ["conversation", "token"]
    prompt = llm.calls[1]
    assert [m["role"] for m in prompt] == ["system", "user", "assistant", "user"]
    assert prompt[1]["content"] == "validate_token decode token"
    assert "--- pay.py" in prompt[-1]["content"] and "jwt.py" not in prompt[-1]["content"]  # context is fresh
    assert len((await client.get(f"/conversations/{cid}")).json()["messages"]) == 4


# --- no context ---
async def test_no_relevant_context_replies_with_the_fallback_and_never_calls_the_llm(client, ingest, sessions, llm):
    pid = await _ready_project(client, ingest)
    events = _events(await _ask(client, pid, "best beaches in goa"))
    assert [n for n, _ in events] == ["conversation", "no_context"]
    assert events[1][1]["content"] == NO_CONTEXT_REPLY
    assert llm.calls == []
    saved = await _messages(sessions)
    assert [(m.role, m.content, m.gen_state) for m in saved] == [
        ("user", "best beaches in goa", "answered"), ("assistant", NO_CONTEXT_REPLY, None),
    ]


# --- validation and availability ---
@pytest.mark.parametrize("content", ["", "   \n\t ", "x" * 8001])
async def test_invalid_messages_are_rejected_and_nothing_is_created(client, ingest, sessions, llm, content):
    pid = await _ready_project(client, ingest)
    assert (await _ask(client, pid, content)).status_code == 422
    assert await _count(sessions, Conversation) == 0 and await _count(sessions, Message) == 0
    assert llm.calls == []


async def test_message_of_exactly_8000_characters_is_accepted(client, ingest, llm):
    pid = await _ready_project(client, ingest)
    assert (await _ask(client, pid, "validate_token " + "x" * 7985)).status_code == 200


async def test_chat_is_refused_when_no_file_is_ready(client, sessions, llm):
    pid = await _project(client, "Empty")
    assert (await _ask(client, pid, "validate_token")).status_code == 409
    assert await _count(sessions, Conversation) == 0 and llm.calls == []


async def test_unknown_project_and_conversation_are_404(client):
    assert (await _ask(client, str(uuid.uuid4()), "hi")).status_code == 404
    assert (await client.post(f"/conversations/{uuid.uuid4()}/messages", json={"content": "hi"})).status_code == 404
    assert (await client.get(f"/conversations/{uuid.uuid4()}")).status_code == 404


async def test_conversations_are_listed_per_project(client, ingest):
    a = await _ready_project(client, ingest, "A")
    b = await _ready_project(client, ingest, "B")
    await _ask(client, a, "validate_token decode token")
    assert len((await client.get(f"/projects/{a}/conversations")).json()) == 1
    assert (await client.get(f"/projects/{b}/conversations")).json() == []


# --- failures ---
async def test_llm_failure_before_streaming_shows_a_generic_error_and_keeps_the_user_message(
    client, ingest, sessions, llm,
):
    pid = await _ready_project(client, ingest)
    llm.fail_before = True
    res = await _ask(client, pid, "validate_token decode token")
    events = _events(res)
    assert [n for n, _ in events] == ["conversation", "error"]
    assert events[1][1] == {"message": GENERIC_ERROR}
    # provider details never reach the client (generated UUIDs are removed first: they can contain "500")
    body = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<id>", res.text)
    assert "500" not in body and "exploded" not in body
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "failed")]


async def test_failure_mid_stream_saves_no_partial_answer(client, ingest, sessions, llm):
    pid = await _ready_project(client, ingest)
    llm.fail_after = 2
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert [n for n, _ in events] == ["conversation", "token", "token", "error"]
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "failed")]


async def test_retrieval_failure_is_the_same_generic_error_and_skips_the_llm(
    client, ingest, sessions, llm, monkeypatch,
):
    pid = await _ready_project(client, ingest)

    async def down(*a, **kw):
        raise chat.RetrievalError("elasticsearch is down (internal)")

    monkeypatch.setattr(chat, "retrieve", down)
    events = _events(await _ask(client, pid, "validate_token decode token"))
    assert events[-1] == ("error", {"message": GENERIC_ERROR})
    assert llm.calls == [] and [m.gen_state for m in await _messages(sessions)] == ["failed"]


async def test_failed_question_is_not_sent_as_history_later(client, ingest, llm):
    pid = await _ready_project(client, ingest)
    llm.fail_before = True
    cid = _events(await _ask(client, pid, "validate_token decode token"))[0][1]["conversation_id"]
    llm.fail_before = False
    await client.post(f"/conversations/{cid}/messages", json={"content": "charge_card invoice amount"})
    assert [m["role"] for m in llm.calls[-1]] == ["system", "user"]  # the unanswered question is left out


async def test_client_disconnect_marks_the_question_stopped_and_saves_no_answer(client, ingest, sessions, llm):
    pid = await _ready_project(client, ingest)
    async with sessions() as s:
        conv = Conversation(project_id=uuid.UUID(pid), title="t")
        s.add(conv)
        await s.flush()
        user = Message(conversation_id=conv.id, role="user", content="validate_token decode token", gen_state="pending")
        s.add(user)
        await s.commit()
    gen = chat.stream_answer(sessions, uuid.UUID(pid), conv.id, user.id, "validate_token decode token")
    assert (await gen.__anext__()).name == "token"
    await gen.aclose()  # what the server does when the client goes away
    assert [(m.role, m.gen_state) for m in await _messages(sessions)] == [("user", "stopped")]


# --- LLM provider wrapper ---
def _sse_body(*deltas):
    lines = [
        "data: " + json.dumps({"id": "1", "object": "chat.completion.chunk", "created": 0, "model": "m",
                               "choices": [{"index": 0, "delta": d, "finish_reason": None}]})
        for d in deltas
    ]
    return ("\n\n".join(lines) + "\n\ndata: [DONE]\n\n").encode()


class _RecordingLimiter(RateLimiter):
    def __init__(self):
        super().__init__()
        self.acquired: list[tuple[int, int]] = []

    async def acquire(self, requests=1, tokens=0):
        self.acquired.append((requests, tokens))


async def test_provider_streams_only_content_and_reserves_prompt_plus_output_tokens():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse_body(
            {"role": "assistant", "reasoning": "let me think"}, {"content": "Hello"}, {"content": " world"},
        ))

    limiter = _RecordingLimiter()
    provider = OpenAICompatLLMProvider(
        "groq", "key", "openai/gpt-oss-120b", 500, 0, 5.0, limiter=limiter, transport=httpx.MockTransport(handler),
    )
    messages = [{"role": "system", "content": "s" * 400}, {"role": "user", "content": "q" * 40}]
    assert [t async for t in provider.stream(messages)] == ["Hello", " world"]  # reasoning is never forwarded
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["body"]["max_completion_tokens"] == 500 and seen["body"]["stream"] is True
    assert limiter.acquired == [(1, (100 + 4) + (10 + 4) + 500)]


async def test_provider_maps_http_errors_to_llm_error():
    provider = OpenAICompatLLMProvider(
        "gemini", "key", "m", 100, 0, 5.0,
        transport=httpx.MockTransport(lambda r: httpx.Response(500, json={"error": {"message": "boom"}})),
    )
    with pytest.raises(LLMError):
        [t async for t in provider.stream([{"role": "user", "content": "hi"}])]


async def test_provider_without_a_key_fails_before_any_request():
    provider = OpenAICompatLLMProvider("groq", "", "m", 100, 0, 5.0, transport=httpx.MockTransport(lambda r: 1 / 0))
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        [t async for t in provider.stream([{"role": "user", "content": "hi"}])]


@pytest.mark.parametrize("name, key", [("groq", "groq_llm"), ("gemini", "gemini_llm")])
def test_configured_provider_uses_its_shared_limiter(monkeypatch, name, key):
    set_llm_provider(None)
    reset_limiters()
    monkeypatch.setattr(settings, "llm_provider", name)
    provider = get_llm_provider()
    primary = getattr(provider, "primary", provider)  # groq is wrapped by the same-provider router
    assert provider.provider == name and primary.limiter is get_limiter(key)
    set_llm_provider(None)
    reset_limiters()


def test_groq_tokens_per_minute_defaults_to_the_keys_8000_limit_with_headroom():
    assert settings.groq_llm_tpm == 7000


async def test_second_long_question_waits_for_the_token_window_instead_of_failing():
    clock = {"t": 0.0}
    slept: list[float] = []

    async def sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    limiter = RateLimiter(tokens_per_minute=7000, clock=lambda: clock["t"], sleep=sleep)
    provider = OpenAICompatLLMProvider(
        "groq", "key", "m", 1500, 0, 5.0, limiter=limiter,
        transport=httpx.MockTransport(lambda r: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=_sse_body({"content": "ok"}))),
    )
    long_prompt = [{"role": "user", "content": "x" * 16000}]  # about 4,000 tokens + 1,500 reserved
    assert [t async for t in provider.stream(long_prompt)] == ["ok"]
    assert slept == []
    assert [t async for t in provider.stream(long_prompt)] == ["ok"]  # 5,500 + 5,500 > 7,000: must wait
    assert sum(slept) >= 60 - 1e-6


# --- pure helpers ---
@pytest.mark.parametrize("question, title", [
    ("How does authentication work?", "How does authentication work"),
    ("can you explain the retry logic?", "The retry logic"),
    ("where is the payment retry implemented in the checkout service module and who calls it from the api layer?",
     "Where is the payment retry implemented in the checkout…"),
    ("???", "New conversation"),
])
def test_title_is_derived_from_the_first_question_without_an_llm(question, title):
    assert title_from_question(question) == title


def _c(path, content, start=1, end=2):
    return Candidate("id", "fid", path, content, start, end, None, None, 10, 0.0, None, None)


def test_context_builder_labels_chunks_and_caps_history(monkeypatch):
    monkeypatch.setattr(settings, "chat_history_messages", 3)
    monkeypatch.setattr(settings, "chat_history_tokens", 50)
    history = [("user", "old question"), ("assistant", "old answer"), ("user", "q2"), ("assistant", "a" * 1000)]
    kept = recent_history(history)
    assert kept[-1]["role"] == "assistant" and len(kept[-1]["content"]) <= 50 * 4 + 2  # newest, cut to fit
    msgs = build_messages("Q?", [_c("a.py", "code")], [("user", "hi"), ("assistant", "hello")])
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[-1]["content"] == "Project context:\n--- a.py, lines 1-2 ---\ncode\n\nQuestion:\nQ?"


# --- same-provider routing (per-model rate limits) ---
class _Stub:
    provider = "groq"

    def __init__(self, model, wait):
        self.model, self.wait, self.used = model, wait, 0

    def wait_estimate(self, messages, max_output_tokens=None):
        return self.wait

    async def stream(self, messages, max_output_tokens=None):
        self.used += 1
        yield self.model


MSG = [{"role": "user", "content": "hi"}]


async def test_router_keeps_the_primary_model_while_its_budget_has_room():
    from app.providers.llm import RoutingLLMProvider
    primary, fallback = _Stub("big", 0.0), _Stub("small", 0.0)
    router = RoutingLLMProvider(primary, fallback, max_wait=6)
    assert [t async for t in router.stream(MSG)] == ["big"] and fallback.used == 0


async def test_router_uses_the_fallback_model_instead_of_a_long_wait():
    from app.providers.llm import RoutingLLMProvider
    primary, fallback = _Stub("big", 40.0), _Stub("small", 0.0)
    router = RoutingLLMProvider(primary, fallback, max_wait=6)
    assert router.wait_estimate(MSG) == 0.0
    assert [t async for t in router.stream(MSG)] == ["small"]


async def test_router_stays_on_the_primary_when_the_fallback_would_wait_longer():
    from app.providers.llm import RoutingLLMProvider
    router = RoutingLLMProvider(_Stub("big", 20.0), _Stub("small", 50.0), max_wait=6)
    assert [t async for t in router.stream(MSG)] == ["big"]


def test_groq_gets_a_fallback_model_with_its_own_limiter_and_gemini_does_not(monkeypatch):
    set_llm_provider(None)
    reset_limiters()
    monkeypatch.setattr(settings, "llm_provider", "groq")
    router = get_llm_provider()
    assert router.fallback.model == "openai/gpt-oss-20b" and router.model == settings.groq_llm_model
    assert router.fallback.limiter is get_limiter("groq_llm_fallback") and router.fallback.limiter is not router.primary.limiter
    set_llm_provider(None)
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    assert not hasattr(get_llm_provider(), "fallback")
    set_llm_provider(None)
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_fallback_model", "")
    assert not hasattr(get_llm_provider(), "fallback")
    set_llm_provider(None)
    reset_limiters()
