"""Chat endpoints (Phase 7). A new chat is only a blank client state: the conversation row is created
by the first message, titled from the question. Both POST endpoints save the user message first and
then stream Server-Sent Events: `conversation` -> [`waiting`] `token`* -> `sources` -> `done` | `no_context` | `error`.
Retry, Cancel (dismiss), delete and the dev Context Inspector are in the second half of this file."""
import json
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db import get_session, get_session_factory
from app.generation import inspector
from app.generation.chat import stream_answer
from app.generation.prompts import title_from_question
from app.models import Conversation, File, Message, Project
from app.schemas import ChatRequest, ConversationDetail, ConversationOut, MessageOut

router = APIRouter(tags=["chat"])


async def _require_chat_ready(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Chat needs at least one READY file; the UI shows why it is disabled, this keeps the API honest."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    ready = await session.scalar(
        select(func.count()).where(File.project_id == project_id, File.status == "ready")
    )
    if project.status == "needs_reindex" or not ready:
        raise HTTPException(status.HTTP_409_CONFLICT, "No files are ready yet.")


def _sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def _events(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID, conversation: Conversation,
    user_message_id: uuid.UUID, question: str,
) -> AsyncIterator[str]:
    yield _sse("conversation", {
        "conversation_id": str(conversation.id), "title": conversation.title,
        "user_message_id": str(user_message_id),
    })
    async for event in stream_answer(sessions, project_id, conversation.id, user_message_id, question):
        yield _sse(event.name, event.data)


def _stream(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(project_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    if await session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return (await session.scalars(
        select(Conversation).where(Conversation.project_id == project_id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    )).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Restores a chat from the database: no retrieval, no LLM call."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    messages = (await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )).all()
    return ConversationDetail(
        id=conversation.id, project_id=conversation.project_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at,
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.post("/projects/{project_id}/conversations")
async def start_conversation(
    project_id: uuid.UUID, body: ChatRequest,
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """First message of a new chat: creates the conversation, then answers."""
    await _require_chat_ready(session, project_id)
    conversation = Conversation(project_id=project_id, title=title_from_question(body.content))
    session.add(conversation)
    await session.flush()
    user_message = Message(
        conversation_id=conversation.id, role="user", content=body.content, gen_state="pending",
    )
    session.add(user_message)
    await session.commit()
    return _stream(_events(sessions, project_id, conversation, user_message.id, body.content))


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID, body: ChatRequest,
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    await _require_chat_ready(session, conversation.project_id)
    user_message = Message(
        conversation_id=conversation.id, role="user", content=body.content, gen_state="pending",
    )
    session.add(user_message)
    await session.commit()
    return _stream(_events(sessions, conversation.project_id, conversation, user_message.id, body.content))


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Removes the conversation and its messages only; the project's files and chunks are untouched."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    await session.delete(conversation)
    await session.commit()
    inspector.forget(conversation_id)


async def _latest_unanswered(session: AsyncSession, message_id: uuid.UUID) -> tuple[Message, Conversation]:
    """The user message a Retry / Cancel applies to: the newest message of its conversation, stopped or failed."""
    message = await session.get(Message, message_id)
    if message is None or message.role != "user":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    newest = await session.scalar(
        select(Message.id).where(Message.conversation_id == message.conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(1)
    )
    if newest != message.id or message.gen_state not in ("failed", "stopped"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Nothing to retry for this message")
    conversation = await session.get(Conversation, message.conversation_id)
    return message, conversation


@router.post("/messages/{message_id}/retry")
async def retry_message(
    message_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Runs retrieval and the LLM again for the same user message (no duplicate is created)."""
    message, conversation = await _latest_unanswered(session, message_id)
    await _require_chat_ready(session, conversation.project_id)
    message.gen_state = "pending"
    await session.commit()
    return _stream(_events(sessions, conversation.project_id, conversation, message.id, message.content))


@router.post("/messages/{message_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_message(message_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Cancel: clears the stopped / failed state; the user message stays in the conversation."""
    message, _ = await _latest_unanswered(session, message_id)
    message.gen_state = "dismissed"
    await session.commit()


@router.get("/conversations/{conversation_id}/context")
async def conversation_context(conversation_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Dev-only Context Inspector: the exact prompt of the conversation's latest turn, plus its memory state."""
    if not settings.dev_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return {
        "turn": inspector.last(conversation_id),
        "summary": conversation.summary,
        "summary_checkpoint_message_id": conversation.summary_checkpoint_message_id,
        "recent_token_estimate": conversation.recent_token_estimate,
        "summary_threshold_tokens": settings.summary_threshold_tokens,
    }
