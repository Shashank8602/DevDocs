"""Query preparation, pure functions.

`keyword_query` turns a natural-language question into the terms BM25 should score: question words and
other stopwords only dilute a lexical match, while identifiers, file names and config keys carry it.
`retrieval_query` makes a follow-up ("where is that token generated?") searchable on its own by adding the
previous question, because retrieval is fresh for every question and never sees the conversation."""
import re

_STOPWORDS = frozenset("""
a an the is are was were be been being do does did doing how what where when why which who whom this that
these those it its i me my we our you your they them their of in on at to for from by with about into over
under can could should would will shall may might must and or but if then than so as not no yes there here
please tell show explain give find work works working used use using get gets make makes made
""".split())

_TOKEN = re.compile(r"[A-Za-z0-9_.\-/]+")

# A follow-up refers back to something said earlier. Only reference words trigger this (never mere
# shortness): an unrelated short question must not borrow the previous question's topic and slip past
# the relevance gate.
_REFERENCE = re.compile(
    r"\b(it|its|that|this|those|these|they|them|their|there|same|above|previous|earlier|former|latter)\b"
    r"|^\s*(and|also|then|so|but|what about|how about|why)\b",
    re.I,
)


def keyword_query(question: str) -> str:
    kept = [w for w in _TOKEN.findall(question) if w.lower() not in _STOPWORDS]
    return " ".join(kept) or question


def is_follow_up(question: str) -> bool:
    return bool(_REFERENCE.search(question))


def retrieval_query(question: str, previous_questions: list[str]) -> str:
    """The text retrieval searches with. A standalone question is returned unchanged."""
    if not previous_questions or not is_follow_up(question):
        return question
    return f"{previous_questions[-1].strip()}\n{question}"
