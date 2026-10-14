"""One chat turn: fresh retrieval -> (no context: fixed reply, no LLM call) -> context -> streamed answer.

`stream_answer` is an async generator of SSE events, independent of HTTP so it can be tested directly:

    [waiting] token*  ->  sources -> done | no_context | error

The user message is already saved (by the API) when it starts. The assistant message (with its frozen
citations) is saved only after the stream completes, in one transaction with the user message's
`gen_state=answered`. A generation that fails ends `gen_state=failed`; one cancelled because the client went
away (Stop) ends `gen_state=stopped`; in both cases nothing of the partial answer is stored. Short sessions
are opened per step, so no database connection is held while the LLM streams.

Resilience: a failure before the first token is retried silently (`generation_retries`), a stream that goes
silent for `llm_inactivity_seconds` is treated as failed, and the user only ever sees the generic error."""
import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.generation import inspector, memory
from app.generation.citations import build_citations
from app.generation.context_builder import build_messages
from app.generation.prompts import GENERIC_ERROR, NO_CONTEXT_REPLY
from app.models import Conversation, Message
from app.providers.llm import LLMError, estimate_prompt_tokens, get_llm_provider
from app.retrieval.pipeline import NoRelevantContext, RetrievalError, retrieve
from app.retrieval.query import retrieval_query

log = logging.getLogger("devdocs.chat")


@dataclass
class Event:
    name: str  # waiting | token | sources | done | no_context | error
    data: dict = field(default_factory=dict)


def _fault(kind: str, attempt: int = 0) -> bool:
    """Dev-only fault injection: "once" fails the first attempt of a turn, "always" fails every attempt."""
    return settings.dev_mode and (kind == "always" or (kind == "once" and attempt == 0))


async def _save_answer(
    sessions: async_sessionmaker[AsyncSession], conversation_id: uuid.UUID, user_message_id: uuid.UUID,
    text: str, sources: list[dict] | None = None,
) -> uuid.UUID:
    async with sessions() as session:
        answer = Message(conversation_id=conversation_id, role="assistant", content=text, sources_json=sources)
        session.add(answer)
        await session.execute(update(Message).where(Message.id == user_message_id).values(gen_state="answered"))
        await session.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(updated_at=func.now())
        )
        await session.commit()
        return answer.id


async def _set_state(sessions: async_sessionmaker[AsyncSession], user_message_id: uuid.UUID, state: str) -> None:
    async with sessions() as session:
        await session.execute(update(Message).where(Message.id == user_message_id).values(gen_state=state))
        await session.commit()


async def _generate(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    """The LLM's tokens, with an inactivity timeout. Raises LLMError on failure or silence."""
    # A producer task feeds a queue and the consumer waits on the queue with a timeout. Cancelling a
    # `wait_for` around an async generator's `__anext__` leaves that generator mid-flight and un-closable;
    # a queue wait is safe to cancel, and cancelling the producer closes the provider stream cleanly.
    queue: asyncio.Queue = asyncio.Queue()

    async def pump() -> None:
        try:
            async for token in get_llm_provider().stream(messages):
                await queue.put(("token", token))
            await queue.put(("end", None))
        except Exception as exc:  # delivered to the consumer; CancelledError passes through untouched
            await queue.put(("error", exc))

    producer = asyncio.create_task(pump())
    try:
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), settings.llm_inactivity_seconds)
            except asyncio.TimeoutError as exc:
                raise LLMError(f"no output for {settings.llm_inactivity_seconds:.0f}s") from exc
            if kind == "token":
                yield value
            elif kind == "end":
                return
            else:
                raise value if isinstance(value, LLMError) else LLMError(f"stream failed: {value!r}")
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)


async def stream_answer(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID, conversation_id: uuid.UUID,
    user_message_id: uuid.UUID, question: str,
) -> AsyncIterator[Event]:
    end_state = "failed"  # what the user message ends as unless the turn completes or is stopped
    started = time.perf_counter()
    try:
        async with sessions() as session:
            summary, history = await memory.load_memory(session, conversation_id, exclude=user_message_id)
            # fresh retrieval for every question; a follow-up borrows the previous question's topic
            search_text = retrieval_query(question, [c for r, c in history if r == "user"])
            if _fault(settings.fault_retrieval):
                raise RetrievalError("injected retrieval fault")
            result = await retrieve(session, project_id, search_text)
        retrieved_ms = (time.perf_counter() - started) * 1000

        if isinstance(result, NoRelevantContext):
            log.info("LLM not called for conversation %s: %s", conversation_id, result.reason)
            message_id = await _save_answer(sessions, conversation_id, user_message_id, NO_CONTEXT_REPLY)
            end_state = "answered"
            yield Event("no_context", {"message_id": str(message_id), "content": NO_CONTEXT_REPLY})
            return

        messages = build_messages(question, result, history, summary)
        sources = build_citations(result)
        inspector.record(
            conversation_id, question=question, search_text=search_text, messages=messages,
            chunks=[{"file_path": c.file_path, "line_start": c.line_start, "line_end": c.line_end,
                     "tokens": c.token_count, "vector_score": c.vector_score} for c in result],
            summary_used=bool(summary), prompt_tokens=estimate_prompt_tokens(messages),
        )

        wait = get_llm_provider().wait_estimate(messages)
        if wait >= 1:  # the provider's rate limit makes this call wait: say so instead of looking frozen
            yield Event("waiting", {"seconds": round(wait)})

        parts: list[str] = []
        first_token_ms: float | None = None
        for attempt in range(settings.generation_retries + 1):
            try:
                if _fault(settings.fault_llm, attempt):
                    raise LLMError("injected LLM fault")
                async for token in _generate(messages):
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - started) * 1000
                    parts.append(token)
                    yield Event("token", {"text": token})
                break
            except LLMError as exc:
                if parts or attempt == settings.generation_retries:
                    raise  # after the first token nothing can be retried invisibly
                log.warning("LLM attempt %d failed before the first token (%s); retrying", attempt + 1, exc)
                await asyncio.sleep(min(0.5 * 2 ** attempt, 2.0))
        answer = "".join(parts).strip()
        if not answer:
            raise LLMError("the model returned no content")
        message_id = await _save_answer(sessions, conversation_id, user_message_id, answer, sources)
        end_state = "answered"
        log.info(
            "answered conversation %s: retrieval %.0f ms, first token %.0f ms, total %.0f ms, %d chunks",
            conversation_id, retrieved_ms, first_token_ms or 0, (time.perf_counter() - started) * 1000, len(result),
        )
        memory.schedule(sessions, conversation_id)
        yield Event("sources", {"sources": sources})
        yield Event("done", {"message_id": str(message_id)})
    except (asyncio.CancelledError, GeneratorExit):
        end_state = "stopped"  # the client went away (Stop): nothing of the partial answer is saved
        raise
    except (RetrievalError, LLMError) as exc:
        log.warning("generation failed for conversation %s: %s", conversation_id, exc)
        yield Event("error", {"message": GENERIC_ERROR})  # never the provider's or the pipeline's wording
    except Exception:
        log.exception("unexpected error answering conversation %s", conversation_id)
        yield Event("error", {"message": GENERIC_ERROR})
    finally:
        if end_state != "answered":
            try:
                await asyncio.shield(_set_state(sessions, user_message_id, end_state))
            except Exception:
                log.exception("could not record gen_state=%s for message %s", end_state, user_message_id)
