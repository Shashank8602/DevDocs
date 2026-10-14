"""Conversation memory (Phase 9). After each saved answer the messages after the summary's checkpoint are
counted; past `summary_threshold_tokens` the older ones are folded into `conversations.summary` by the
chat LLM, in the background, and the checkpoint moves forward. The newest `summary_keep_messages` stay
verbatim. The summary call goes through the same provider wrapper, so it shares the rate-limit window.

A failed summary only means the next answer carries a longer history; nothing user-facing breaks."""
import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.generation.prompts import SUMMARY_PROMPT
from app.models import Conversation, Message
from app.providers.llm import get_llm_provider
from app.providers.rate_limit import estimate_tokens

log = logging.getLogger("devdocs.memory")

_running: set[uuid.UUID] = set()
_tasks: set[asyncio.Task] = set()
_TRANSCRIPT_CHARS = 1500  # per message, so one huge answer cannot blow the summary prompt


def _tokens(content: str) -> int:
    return estimate_tokens(content) + 4


def completed(messages: list[Message]) -> list[Message]:
    """Messages that are part of the dialogue: answers, and questions that got one."""
    return [m for m in messages if m.role == "assistant" or m.gen_state == "answered"]


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
    rows = [m for m in await _all_messages(session, conversation_id) if m.id != exclude]
    checkpoint = conversation.summary_checkpoint_message_id if conversation else None
    recent = after_checkpoint(completed(rows), checkpoint)
    return (conversation.summary if conversation else None), [(m.role, m.content) for m in recent]


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
        recent = after_checkpoint(
            completed(await _all_messages(session, conversation_id)), conversation.summary_checkpoint_message_id,
        )
        conversation.recent_token_estimate = sum(_tokens(m.content) for m in recent)
        await session.commit()
        if conversation.recent_token_estimate <= settings.summary_threshold_tokens:
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
        remaining = after_checkpoint(completed(await _all_messages(session, conversation_id)), checkpoint)
        conversation.recent_token_estimate = sum(_tokens(m.content) for m in remaining)
        await session.commit()
    log.info("summarized %d messages of conversation %s", len(fold), conversation_id)
    return True
