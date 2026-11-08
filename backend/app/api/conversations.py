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

from app import access
from app.api.files import disk_path
from app.auth_deps import current_user, dev_admin_only, file_for, not_previewing, project_for
from app.config import settings
from app.db import get_session, get_session_factory
from app.generation import inspector
from app.generation.chat import stream_answer, stream_selection_answer
from app.generation.prompts import title_from_question
from app.models import Conversation, Message, User
from app.retrieval.selection import Candidate
from app.schemas import ChatRequest, ConversationDetail, ConversationOut, FileAskRequest, MessageOut, Selection

router = APIRouter(tags=["chat"])


async def _require_chat_ready(session: AsyncSession, user: User, project_id: uuid.UUID) -> None:
    """Chat needs at least one READY file the user may read; the UI shows why it is disabled, this keeps the
    API honest. A project the user cannot read anything in does not exist for them (404)."""
    project = await project_for(session, user, project_id)
    ready = len(await access.readable_files(session, user, project_id, ready_only=True))
    if project.status == "needs_reindex" or not ready:
        raise HTTPException(status.HTTP_409_CONFLICT, "No files are ready yet.")


async def _owned(session: AsyncSession, user: User, conversation_id: uuid.UUID) -> Conversation:
    """A conversation belongs to the person who started it and to nobody else, admins included.
    Anything else answers 404, the same as a conversation that does not exist."""
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return conversation


UNAVAILABLE_LABEL = "Source no longer available"
MAX_SELECTED_LINES = 200
MAX_SELECTED_CHARS = 6000  # about 1,500 tokens: the selection must fit the free-tier token budget


async def _pinned(
    session: AsyncSession, user: User, project_id: uuid.UUID, selection: Selection | None,
) -> tuple[Candidate | None, str]:
    """For "ask about these lines": the selected lines as a context chunk, plus the note added to the stored question.
    The file must be in this project and readable by the asker (404 otherwise, like any hidden file); the lines are
    read from the stored file here, never taken from the client."""
    if selection is None:
        return None, ""
    row = await file_for(session, user, selection.file_id)
    if row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    try:
        lines = disk_path(row.project_id, row.file_path).read_text("utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from exc
    start = min(selection.line_start, max(len(lines), 1))
    end = min(max(selection.line_end, start), start + MAX_SELECTED_LINES - 1, max(len(lines), 1))
    text = "\n".join(lines[start - 1 : end])[:MAX_SELECTED_CHARS]
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The selection is empty.")
    chunk = Candidate(
        chunk_id=f"selection-{row.id}-{start}", file_id=str(row.id), file_path=row.file_path, content=text,
        line_start=start, line_end=end, page_number=None, section=None, token_count=max(len(text) // 4, 1),
        rrf_score=1.0, bm25_rank=None, vector_rank=None, file_type=row.file_type,
    )
    return chunk, f"\n\n(Selected: {row.file_path}, lines {start}-{end})"


def _recheck_sources(sources: list | None, allowed: set[str]) -> list | None:
    """Citations are frozen when an answer is saved. On display, one pointing at a file the person can no
    longer read is replaced by a stub that names nothing and cannot be opened."""
    if sources is None:
        return None
    out = []
    for src in sources:
        if isinstance(src, dict) and str(src.get("file_id")) in allowed:
            out.append({**src, "available": True})
        else:
            out.append({"file_id": None, "file_path": "", "file_type": None, "ranges": [],
                        "label": UNAVAILABLE_LABEL, "available": False})
    return out


def _sse(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


async def _events(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID, conversation: Conversation,
    user_message_id: uuid.UUID, question: str, user_id: uuid.UUID, pinned: Candidate | None = None,
) -> AsyncIterator[str]:
    yield _sse("conversation", {
        "conversation_id": str(conversation.id), "title": conversation.title,
        "user_message_id": str(user_message_id),
    })
    async for event in stream_answer(sessions, project_id, conversation.id, user_message_id, question, user_id, pinned):
        yield _sse(event.name, event.data)


async def _selection_events(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID, user_id: uuid.UUID, question: str,
    pinned: Candidate,
) -> AsyncIterator[str]:
    async for event in stream_selection_answer(sessions, project_id, user_id, question, pinned):
        yield _sse(event.name, event.data)


def _stream(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events, media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/projects/{project_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(
    project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    await project_for(session, user, project_id)
    return (await session.scalars(
        select(Conversation).where(Conversation.project_id == project_id, Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.created_at.desc())
    )).all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Restores a chat from the database: no retrieval, no LLM call. Citations are re-checked against what
    the person can read now."""
    conversation = await _owned(session, user, conversation_id)
    allowed = {str(i) for i in await access.readable_file_ids(session, user, conversation.project_id)}
    messages = (await session.scalars(
        select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
    )).all()
    return ConversationDetail(
        id=conversation.id, project_id=conversation.project_id, title=conversation.title,
        created_at=conversation.created_at, updated_at=conversation.updated_at,
        messages=[
            MessageOut.model_validate(m).model_copy(update={"sources_json": _recheck_sources(m.sources_json, allowed)})
            for m in messages
        ],
    )


@router.post("/projects/{project_id}/conversations")
async def start_conversation(
    project_id: uuid.UUID, body: ChatRequest,
    user: User = Depends(not_previewing),
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """First message of a new chat: creates the conversation, then answers."""
    await _require_chat_ready(session, user, project_id)
    pinned, note = await _pinned(session, user, project_id, body.selection)
    conversation = Conversation(
        project_id=project_id, user_id=user.id, title=title_from_question(body.content),
    )
    session.add(conversation)
    await session.flush()
    user_message = Message(
        conversation_id=conversation.id, role="user", content=body.content + note, gen_state="pending",
    )
    session.add(user_message)
    await session.commit()
    return _stream(_events(sessions, project_id, conversation, user_message.id, body.content + note, user.id, pinned))


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: uuid.UUID, body: ChatRequest,
    user: User = Depends(not_previewing),
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    conversation = await _owned(session, user, conversation_id)
    await _require_chat_ready(session, user, conversation.project_id)
    pinned, note = await _pinned(session, user, conversation.project_id, body.selection)
    user_message = Message(
        conversation_id=conversation.id, role="user", content=body.content + note, gen_state="pending",
    )
    session.add(user_message)
    await session.commit()
    return _stream(_events(
        sessions, conversation.project_id, conversation, user_message.id, body.content + note, user.id, pinned,
    ))


@router.post("/files/{file_id}/ask")
async def ask_about_lines(
    file_id: uuid.UUID, body: FileAskRequest,
    user: User = Depends(not_previewing),
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Select and ask, answered in the file viewer. Ephemeral: no conversation, no stored message; the stream is
    `[waiting] token* sources done | error`. A file the person may not read is a 404, like everywhere else."""
    row = await file_for(session, user, file_id)
    await _require_chat_ready(session, user, row.project_id)
    pinned, _ = await _pinned(
        session, user, row.project_id,
        Selection(file_id=file_id, line_start=body.line_start, line_end=body.line_end),
    )
    assert pinned is not None
    return _stream(_selection_events(sessions, row.project_id, user.id, body.content, pinned))


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: uuid.UUID, user: User = Depends(not_previewing), session: AsyncSession = Depends(get_session),
):
    """Removes the conversation and its messages only; the project's files and chunks are untouched."""
    conversation = await _owned(session, user, conversation_id)
    await session.delete(conversation)
    await session.commit()
    inspector.forget(conversation_id)


async def _latest_unanswered(
    session: AsyncSession, user: User, message_id: uuid.UUID,
) -> tuple[Message, Conversation]:
    """The user message a Retry / Cancel applies to: the newest message of its conversation, stopped or failed."""
    message = await session.get(Message, message_id)
    if message is None or message.role != "user":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    await _owned(session, user, message.conversation_id)
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
    user: User = Depends(not_previewing),
    session: AsyncSession = Depends(get_session),
    sessions: async_sessionmaker[AsyncSession] = Depends(get_session_factory),
):
    """Runs retrieval and the LLM again for the same user message (no duplicate is created)."""
    message, conversation = await _latest_unanswered(session, user, message_id)
    await _require_chat_ready(session, user, conversation.project_id)
    message.gen_state = "pending"
    await session.commit()
    return _stream(_events(sessions, conversation.project_id, conversation, message.id, message.content, user.id))


@router.post("/messages/{message_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss_message(
    message_id: uuid.UUID, user: User = Depends(not_previewing), session: AsyncSession = Depends(get_session),
):
    """Cancel: clears the stopped / failed state; the user message stays in the conversation."""
    message, _ = await _latest_unanswered(session, user, message_id)
    message.gen_state = "dismissed"
    await session.commit()


@router.get("/conversations/{conversation_id}/context")
async def conversation_context(
    conversation_id: uuid.UUID, user: User = Depends(dev_admin_only), session: AsyncSession = Depends(get_session),
):
    """Dev-only Context Inspector: the exact prompt of the conversation's latest turn, plus its memory state.
    Admin only, and only for the admin's own conversations."""
    if not settings.dev_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    conversation = await _owned(session, user, conversation_id)
    return {
        "turn": inspector.last(conversation_id),
        "summary": conversation.summary,
        "summary_checkpoint_message_id": conversation.summary_checkpoint_message_id,
        "recent_token_estimate": conversation.recent_token_estimate,
        "summary_threshold_tokens": settings.summary_threshold_tokens,
    }
