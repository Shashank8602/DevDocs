# Future Enhancement: Caching

**Status: not in V1.** Nothing here is built or scheduled. This records the analysis so it can be picked up after V1 if profiling or usage justifies it.

## Why V1 has no cache

- Single-user local app. Postgres is on localhost and the lookups are indexed, so reads take milliseconds.
- The retrieval hot path is Elasticsearch (BM25 + kNN) and RRF in application code. Postgres is only read for the project's `ready` file IDs and for chunk/citation metadata.
- The LLM call dominates latency and cost, not DB reads.
- Caching project state conflicts with the isolation invariant: retrieval must always filter by `project_id` and the *current* Postgres `ready` file IDs. A stale cache could return chunks from deleted or half-ingested files.
- Cached results make the Retrieval Playground, Context Inspector and `eval.run` (Recall@5 / MRR) less deterministic.

## Candidates, in priority order

| # | Candidate | Value | Invalidation | Verdict |
|---|---|---|---|---|
| 1 | **Query-embedding LRU** (in memory, keyed by text + embedding fingerprint) | Saves a rate-limited Gemini embedding call and ~100–300 ms on repeated/retried queries | None: the key includes the fingerprint | Do first |
| 2 | **Decrypted settings / API key** (in memory) | Avoids reading and decrypting on every request | Clear on settings update | Do |
| 3 | **Reranker model loaded once at startup** | Avoids reloading the fastembed cross-encoder | None | Do (likely already implied) |
| 4 | **Response cache** (exact-match answers) | Skips the whole pipeline including the LLM call | Automatic via the key (see below) | Optional, later |
| 5 | `ready` file-ID set / chunk metadata | A few ms per turn | Must be cleared on every ingest-state change | Skip: small gain, real staleness risk |
| 6 | Redis | None at this scale | n/a | Skip: another container for no benefit |

## Response cache design (candidate 4)

Return a stored answer when the same valid question is asked again.

**Key** must contain everything the answer depends on, so stale entries are simply never looked up:

- `project_id`
- **project content version**: a counter incremented whenever any file becomes `ready`, changes or is deleted
- embedding fingerprint, LLM model and prompt version
- the normalized question text

**Rules**

1. Exact match only. No semantic/fuzzy matching: a wrong cached answer with confident citations is worse than a slow correct one.
2. First-turn questions only. Follow-ups depend on chat history and can never be valid hits.
3. Cache only fully completed answers. Never cache stopped or failed generations, and never cache `NoRelevantContext`.
4. Store in Postgres, so entries are durable and can reference the original message. No Redis.
5. A cache hit still replays through the normal SSE events (stored text streamed back, same citations) so the UI path is unchanged, and still saves the assistant message only after the replay completes.
6. Bypass switches: a "Regenerate" action for users and a `DEV_MODE` flag for the dev tools and eval.
7. Bound the size (LRU/TTL eviction).

**Cons to accept if built**

- Low expected hit rate for one local user.
- Prompt or retrieval tweaks won't show on already-cached questions (hence the bypasses).
- Extra tests: hit, miss, version bump, bypass, replay.

## When to revisit

- Profiling shows embedding calls or the DB as a bottleneck.
- The app moves to multiple users or a shared deployment.
- Repeated-question usage (demos, evals) makes the hit rate worthwhile.

Measure first, then add the smallest cache that addresses the measured cost.
