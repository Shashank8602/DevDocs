"""Standalone check that the LLM (Groq or Gemini) and Gemini embedding keys work for DevDocs.

Not part of the app: imports nothing from backend/app. Reads settings from the repo-root .env.
The LLM under test is picked by LLM_PROVIDER (groq | gemini) in .env.

Run from the repo root:
    backend\\.venv\\Scripts\\python scripts\\check_providers.py          # active LLM provider + embeddings
    backend\\.venv\\Scripts\\python scripts\\check_providers.py --all    # both LLM providers + embeddings
"""

import json
import math
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

GROQ_KEY = os.getenv("GROQ_API_KEY", "").strip()
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "groq").strip().lower()
EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001").strip()
EMBED_DIMS = int(os.getenv("EMBEDDING_DIMS", "1536"))

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Both LLM providers speak the OpenAI chat-completions protocol; only URL, key and model differ.
LLM_PROVIDERS = {
    "groq": {
        "label": "Groq",
        "key": GROQ_KEY,
        "key_var": "GROQ_API_KEY",
        "model": os.getenv("GROQ_LLM_MODEL", "openai/gpt-oss-120b").strip(),
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "max_tokens_field": "max_completion_tokens",
    },
    "gemini": {
        "label": "Gemini",
        "key": GEMINI_KEY,
        "key_var": "GEMINI_API_KEY",
        "model": os.getenv("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite").strip(),
        "url": f"{GEMINI_BASE}/openai/chat/completions",
        "max_tokens_field": "max_tokens",
    },
}

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def explain_http_error(resp: httpx.Response) -> str:
    try:
        msg = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        msg = resp.text
    hint = {
        400: "bad request (check model name / parameters)",
        401: "key rejected (wrong or revoked key)",
        403: "key lacks access to this model/API",
        404: "model or endpoint not found",
        429: "rate limited / quota exhausted",
    }.get(resp.status_code, "")
    return f"HTTP {resp.status_code} {hint}: {str(msg)[:300]}"


def post_with_retry(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """POST, retrying on 429 with backoff, like the real providers must."""
    for attempt in range(4):
        resp = client.post(url, **kw)
        if resp.status_code != 429:
            return resp
        wait = float(resp.headers.get("retry-after", 2 ** attempt))
        print(f"    429 received, retrying in {wait:.0f}s (attempt {attempt + 1}/4)")
        time.sleep(wait)
    return resp


# ------------------------------------------------- LLM (Groq or Gemini)
def check_llm(provider: str) -> None:
    cfg = LLM_PROVIDERS[provider]
    label, model, url = cfg["label"], cfg["model"], cfg["url"]
    print(f"\n== {label} chat: {model} ==")
    if not cfg["key"]:
        record(f"{label}: {cfg['key_var']} present", False, "missing in .env")
        return
    record(f"{label}: {cfg['key_var']} present", True)

    headers = {"Authorization": f"Bearer {cfg['key']}"}
    messages = [
        {"role": "system", "content": "You are a concise assistant for a code-documentation tool."},
        {"role": "user", "content": "In one sentence, what does a JWT validation function do?"},
    ]
    # Reasoning models spend tokens thinking before answering, so leave headroom.
    base = {"model": model, "messages": messages, "reasoning_effort": "low",
            cfg["max_tokens_field"]: 800}

    with httpx.Client(timeout=60) as client:
        # 1) plain completion
        r = post_with_retry(client, url, headers=headers, json=base)
        if r.status_code != 200:
            record(f"{label}: non-streaming completion", False, explain_http_error(r))
            return
        data = r.json()
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        usage = data.get("usage", {})
        record(f"{label}: non-streaming completion", bool(content), f"reply: {content[:120]!r}")
        print(f"    tokens: prompt={usage.get('prompt_tokens')} "
              f"completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}")
        for h in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
                  "x-ratelimit-limit-tokens"):
            if h in r.headers:
                print(f"    {h}: {r.headers[h]}")

        # 2) streaming: the chat feature (SSE) needs content deltas separate from reasoning
        content_chunks = reasoning_chunks = 0
        streamed = ""
        t0 = time.perf_counter()
        first_token_at = None
        with client.stream("POST", url, headers=headers, json={**base, "stream": True}) as s:
            if s.status_code != 200:
                s.read()
                record(f"{label}: streaming completion", False, explain_http_error(s))
                return
            for line in s.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                choices = json.loads(payload).get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                if delta.get("reasoning"):
                    reasoning_chunks += 1
                if delta.get("content"):
                    content_chunks += 1
                    streamed += delta["content"]
                    if first_token_at is None:
                        first_token_at = time.perf_counter() - t0
        record(f"{label}: streaming completion", content_chunks > 0,
               f"{content_chunks} content chunks, {reasoning_chunks} reasoning chunks, "
               f"first content token after {first_token_at or 0:.2f}s")
        record(f"{label}: content stream is clean (no reasoning text mixed into content)",
               bool(streamed.strip()), f"streamed: {streamed.strip()[:100]!r}")


# ------------------------------------------------------- Gemini (embeddings)
def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def check_gemini() -> None:
    print(f"\n== Gemini embeddings: {EMBED_MODEL} (dims={EMBED_DIMS}) ==")
    if not GEMINI_KEY:
        record("GEMINI_API_KEY present", False, "missing in .env")
        return
    record("GEMINI_API_KEY present", True)

    headers = {"x-goog-api-key": GEMINI_KEY}
    model_path = f"models/{EMBED_MODEL}"

    def embed_batch(texts: list[str], task: str, client: httpx.Client) -> list[list[float]]:
        body = {"requests": [{
            "model": model_path,
            "content": {"parts": [{"text": t}]},
            "taskType": task,
            "outputDimensionality": EMBED_DIMS,
        } for t in texts]}
        r = post_with_retry(client, f"{GEMINI_BASE}/{model_path}:batchEmbedContents",
                            headers=headers, json=body)
        if r.status_code != 200:
            raise RuntimeError(explain_http_error(r))
        return [e["values"] for e in r.json()["embeddings"]]

    docs = [
        "def validate_token(token): verify the JWT signature and expiry, then return the user identity.",
        "def retry_payment(order): charge the card again with exponential backoff after a failure.",
        "The Kerala backwaters are best visited by houseboat during the winter months.",
    ]
    query = "where do we verify the user's identity"

    with httpx.Client(timeout=60) as client:
        try:
            doc_vecs = embed_batch(docs, "RETRIEVAL_DOCUMENT", client)
        except Exception as exc:
            record("batch document embedding", False, str(exc))
            return
        record("batch document embedding", len(doc_vecs) == len(docs), f"{len(doc_vecs)} vectors")
        dim_ok = all(len(v) == EMBED_DIMS for v in doc_vecs)
        record(f"vector length == {EMBED_DIMS}", dim_ok, f"got {len(doc_vecs[0])}")
        norm = math.sqrt(sum(x * x for x in doc_vecs[0]))
        print(f"    L2 norm of first vector: {norm:.3f} "
              f"({'unit length' if abs(norm - 1) < 1e-3 else 'not normalized -> use cosine in ES'})")

        try:
            qvec = embed_batch([query], "RETRIEVAL_QUERY", client)[0]
        except Exception as exc:
            record("query embedding", False, str(exc))
            return
        record("query embedding", len(qvec) == EMBED_DIMS)

    scores = [cosine(qvec, v) for v in doc_vecs]
    for text, sc in zip(docs, scores):
        print(f"    cos={sc:.3f}  {text[:60]}")
    record("semantic ranking: auth snippet is the best match for the query",
           scores.index(max(scores)) == 0,
           "wording differs from the snippet, so this proves real semantic retrieval")


def main() -> int:
    print(f"Using .env at {ROOT / '.env'}")
    if LLM_PROVIDER not in LLM_PROVIDERS:
        print(f"LLM_PROVIDER={LLM_PROVIDER!r} is not valid; use one of {sorted(LLM_PROVIDERS)}")
        return 1
    print(f"Active LLM provider (LLM_PROVIDER): {LLM_PROVIDER}")
    for provider in (LLM_PROVIDERS if "--all" in sys.argv else [LLM_PROVIDER]):
        check_llm(provider)
    check_gemini()

    failed = [r for r in results if not r[1]]
    print("\n== Summary ==")
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for name, _, detail in failed:
        print(f"  FAILED: {name} - {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
