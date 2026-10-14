"""Dev-only Context Inspector store: the exact prompt of each conversation's latest turn, kept in memory
(bounded; lost on restart by design). Nothing here is persisted or shown outside DEV_MODE."""
from collections import OrderedDict
from datetime import datetime, timezone

_MAX = 100
_turns: OrderedDict[str, dict] = OrderedDict()


def record(
    conversation_id, *, question: str, search_text: str, messages: list[dict], chunks: list[dict],
    summary_used: bool, prompt_tokens: int,
) -> None:
    key = str(conversation_id)
    _turns[key] = {
        "question": question, "retrieval_query": search_text, "messages": messages, "chunks": chunks,
        "summary_used": summary_used, "prompt_tokens_estimate": prompt_tokens,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _turns.move_to_end(key)
    while len(_turns) > _MAX:
        _turns.popitem(last=False)


def last(conversation_id) -> dict | None:
    return _turns.get(str(conversation_id))


def forget(conversation_id) -> None:
    _turns.pop(str(conversation_id), None)
