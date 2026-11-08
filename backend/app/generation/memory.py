"""Conversation memory (Phase 9). After each saved answer the messages after the summary's checkpoint are
measured as the prompt would carry them (older answers as short digests); when the window is nearly full, or
the history passes `summary_threshold_tokens`, the older ones are folded into `conversations.summary` by the
chat LLM, in the background, and the checkpoint moves forward. The newest `summary_keep_messages` stay
verbatim. The summary call goes through the same provider wrapper, so it shares the rate-limit window.

A failed summary only means the next answer carries a longer history; nothing user-facing breaks."""
import asyncio
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access
from app.config import settings
from app.generation.prompts import SUMMARY_PROMPT
from app.models import Conversation, Message, User
from app.providers.llm import get_llm_provider
from app.providers.rate_limit import estimate_tokens

log = logging.getLogger("devdocs.memory")

_running: set[uuid.UUID] = set()
_tasks: set[asyncio.Task] = set()
_TRANSCRIPT_CHARS = 1500  # per message, so one huge answer cannot blow the summary prompt


def _tokens(content: str) -> int:
    return estimate_tokens(content) + 4


_FENCE = re.compile(r"```.*?```", re.S)
_SENTENCE_END = (". ", "? ", "! ")


def digest_answer(content: str, sources: list | None) -> str:
    """An earlier answer, shortened for the prompt: code blocks dropped, the first sentences kept, and the files it
    cited. Project facts come from fresh retrieval every turn, so the full text of an old answer is mostly repeated
    tokens. Stored messages are never changed; this only affects what is sent."""
    text = re.sub(r"\s+", " ", _FENCE.sub(" [code] ", content)).strip()
    limit = settings.history_answer_chars
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(s) for s in _SENTENCE_END)
        text = (cut[: end + 1] if end >= limit // 2 else cut.rsplit(" ", 1)[0]) + " …"
    files = list(dict.fromkeys(
        s["file_path"] for s in sources or [] if isinstance(s, dict) and s.get("file_path")
    ))[:3]
    return f"(earlier answer, shortened) {text}" + (f" [cited: {', '.join(files)}]" if files else "")


def history_pairs(messages: list[Message]) -> list[tuple[str, str]]:
    """(role, content) as sent to the model: every answer but the newest is a digest. The newest stays whole,
    because "explain that more" is about it."""
    last = max((i for i, m in enumerate(messages) if m.role == "assistant"), default=-1)
    return [
        (m.role, digest_answer(m.content, m.sources_json)
         if settings.history_digest_answers and m.role == "assistant" and i != last else m.content)
        for i, m in enumerate(messages)
    ]


def window_nearly_full(history: list[tuple[str, str]]) -> bool:
    """True when the next messages would start pushing the oldest out of the prompt window, so folding them into
    the summary is needed now. Until then a summary call (which shares the rate limit) buys nothing."""
    tokens = sum(_tokens(c) for _, c in history)
    return len(history) >= settings.chat_history_messages or tokens >= settings.chat_history_tokens * 0.8


def completed(messages: list[Message]) -> list[Message]:
    """Messages that are part of the dialogue: answers, and questions that got one."""
    return [m for m in messages if m.role == "assistant" or m.gen_state == "answered"]


def drop_unreadable(messages: list[Message], allowed: set[str] | None) -> list[Message]:
    """Leave out every answer that cites a file the person can no longer read, together with the question it
    answered, so neither the prompt history nor a new summary carries text from that file. None = no filter."""
    if allowed is None:
        return messages
    skip: set[int] = set()
    for i, m in enumerate(messages):
        if m.role == "assistant" and any(
            not isinstance(s, dict) or str(s.get("file_id")) not in allowed for s in (m.sources_json or [])
        ):
            skip.add(i)
            if i > 0 and messages[i - 1].role == "user":
                skip.add(i - 1)
    return [m for i, m in enumerate(messages) if i not in skip]


async def allowed_for(session: AsyncSession, conversation: Conversation | None) -> set[str] | None:
    """The file ids the conversation's owner may read now (None for a conversation with no owner)."""
    if conversation is None or conversation.user_id is None:
        return None
    user = await session.get(User, conversation.user_id)
    if user is None:
        return set()
    return {str(i) for i in await access.readable_file_ids(session, user, conversation.project_id)}


def after_checkpoint(messages: list[Message], checkpoint: uuid.UUID | None) -> list[Message]:
    if checkpoint is None:
        return messages
    for i, m in enumerate(messages):
        if m.id == checkpoint:
            return messages[i + 1:]
    return messages  # checkpoint message is gone: nothing can be skipped safely


async def _all_messages(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    return list((await session.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at, Message.id)
    )).scalars().all())


async def load_memory(
    session: AsyncSession, conversation_id: uuid.UUID, exclude: uuid.UUID | None = None,
) -> tuple[str | None, list[tuple[str, str]]]:
    """(summary, recent (role, content) pairs oldest first) for building a prompt. `exclude` is the
    question being answered."""
    conversation = await session.get(Conversation, conversation_id)
    allowed = await allowed_for(session, conversation)
    rows = drop_unreadable([m for m in await _all_messages(session, conversation_id) if m.id != exclude], allowed)
    checkpoint = conversation.summary_checkpoint_message_id if conversation else None
    recent = after_checkpoint(completed(rows), checkpoint)
    return (conversation.summary if conversation else None), history_pairs(recent)


def schedule(sessions: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID) -> None:
    """Run `update_memory` in the background; one run per conversation at a time."""
    if conversation_id in _running:
        return
    _running.add(conversation_id)
    task = asyncio.create_task(_run(sessions, conversation_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _run(sessions: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID) -> None:
    try:
        await update_memory(sessions, conversation_id)
    except Exception as exc:
        log.warning("conversation summary failed for %s: %r", conversation_id, exc)
    finally:
        _running.discard(conversation_id)


async def settle() -> None:
    """Wait for in-flight summaries (tests, shutdown)."""
    if _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def update_memory(sessions: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID) -> bool:
    """Refresh the token estimate and summarize when it passes the threshold. True if a summary was written."""
    async with sessions() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            return False
        allowed = await allowed_for(session, conversation)
        recent = after_checkpoint(
            completed(drop_unreadable(await _all_messages(session, conversation_id), allowed)),
            conversation.summary_checkpoint_message_id,
        )
        sent = history_pairs(recent)  # what the prompt would carry, not the raw stored text
        conversation.recent_token_estimate = sum(_tokens(c) for _, c in sent)
        await session.commit()
        if conversation.recent_token_estimate <= settings.summary_threshold_tokens and not window_nearly_full(sent):
            return False
        fold = recent[: max(len(recent) - settings.summary_keep_messages, 0)]
        while fold and fold[-1].role != "assistant":  # fold whole exchanges: end on an answer
            fold.pop()
        if not fold:
            return False
        previous, transcript = conversation.summary, [(m.role, m.content) for m in fold]
        checkpoint = fold[-1].id

    text = (f"Previous summary:\n{previous}\n\n" if previous else "") + "New messages:\n" + "\n".join(
        f"{'Developer' if role == 'user' else 'DevDocs'}: {content[:_TRANSCRIPT_CHARS]}"
        for role, content in transcript
    )
    parts: list[str] = []
    async for token in get_llm_provider().stream(
        [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": text}],
        max_output_tokens=settings.summary_max_tokens,
    ):
        parts.append(token)
    summary = "".join(parts).strip()
    if not summary:
        return False

    async with sessions() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:  # deleted while summarizing
            return False
        conversation.summary, conversation.summary_checkpoint_message_id = summary, checkpoint
        remaining = after_checkpoint(
            completed(drop_unreadable(await _all_messages(session, conversation_id), allowed)), checkpoint,
        )
        conversation.recent_token_estimate = sum(_tokens(c) for _, c in history_pairs(remaining))
        await session.commit()
    log.info("summarized %d messages of conversation %s", len(fold), conversation_id)
    return True
