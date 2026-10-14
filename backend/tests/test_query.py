"""Query preparation: keyword extraction and follow-up resolution (pure functions)."""
from app.retrieval.query import is_follow_up, keyword_query, retrieval_query


def test_keyword_query_drops_question_words_and_keeps_identifiers():
    assert keyword_query("How does validate_token work in auth.py?") == "validate_token auth.py"
    assert keyword_query("what is the upload limit") == "upload limit"


def test_keyword_query_never_returns_empty():
    assert keyword_query("what is it") == "what is it"


def test_follow_up_needs_a_reference_word_not_shortness():
    assert is_follow_up("where is that token generated?")
    assert is_follow_up("and what about refresh tokens")
    assert not is_follow_up("best beaches in Goa")  # short, but no reference: must not borrow a topic
    assert not is_follow_up("how does authentication work")


def test_retrieval_query_adds_only_the_previous_question():
    prev = ["how does login work", "how are passwords hashed"]
    assert retrieval_query("where is that stored", prev) == "how are passwords hashed\nwhere is that stored"
    assert retrieval_query("best beaches in Goa", prev) == "best beaches in Goa"
    assert retrieval_query("where is that stored", []) == "where is that stored"


# --- test-file prior ---
def _hit(path, score):
    from app.retrieval.fusion import FusedHit
    return FusedHit("c-" + path, "f-" + path, path, "x", score, None, None)


def test_test_files_are_demoted_for_implementation_questions():
    from app.retrieval.fusion import apply_test_prior
    hits = [_hit("backend/tests/test_auth.py", 0.030), _hit("backend/app/auth.py", 0.027), _hit("web/Auth.test.tsx", 0.026)]
    out = apply_test_prior(hits, "how does token validation work", 0.8)
    assert [h.file_path for h in out] == ["backend/app/auth.py", "backend/tests/test_auth.py", "web/Auth.test.tsx"]


def test_questions_about_tests_keep_test_files_in_place():
    from app.retrieval.fusion import apply_test_prior
    hits = [_hit("backend/tests/test_auth.py", 0.030), _hit("backend/app/auth.py", 0.027)]
    out = apply_test_prior(hits, "which tests cover token validation", 0.8)
    assert [h.file_path for h in out] == ["backend/tests/test_auth.py", "backend/app/auth.py"]
    assert out[0].rrf_score == 0.030


def test_prior_is_off_at_factor_one():
    from app.retrieval.fusion import apply_test_prior
    hits = [_hit("tests/test_a.py", 0.03), _hit("a.py", 0.02)]
    assert apply_test_prior(hits, "how does a work", 1.0) is hits
