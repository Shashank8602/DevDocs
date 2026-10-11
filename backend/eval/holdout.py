"""Held-out validation: `python -m eval.holdout` from backend/.

Runs a frozen question set (default eval/dataset_devdocs_holdout.json) that was NOT used to choose any
setting. It refuses to run unless the configured settings equal the values frozen in the dataset file.

For every question it measures, on the real pipeline (Postgres + ES + live Gemini query embedding):
  * the NEW pipeline  : gate -> relative filter (+ BM25 protect) -> overlap / budget / Top-K, reranker off
  * the OLD pipeline  : MiniLM on every candidate, rerank order, chunk threshold -5.5 (the first Phase 6 pipeline)
  * latency           : the live query-embedding call, the search + Postgres part, and the old pipeline's rerank
                        time, so end-to-end p50 / p95 are measured, not estimated.
Both pipelines are replayed from the same recorded candidates, and the production pipeline is also run once per
question (settings as configured) to confirm the replay serves the same files.

Query embeddings are cached (eval/.cache), so this costs one Gemini call per new question and re-runs are free.
A quota error stops cleanly and resumes later."""
import argparse
import asyncio
import json
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.config import settings
from app.db import SessionLocal
from app.providers.embeddings import EmbeddingError, get_embedding_provider, set_embedding_provider
from app.retrieval import pipeline
from app.retrieval.reranker import CrossEncoderReranker, set_reranker
from app.retrieval.selection import select as select_context
from eval.ablation import TimedReranker
from eval.experiments import by_rrf
from eval.run import CachingEmbedder, _find_project, _mean, _norm, _outcome

EVAL_DIR = Path(__file__).parent
DEFAULT = EVAL_DIR / "dataset_devdocs_holdout.json"
OLD_THRESHOLD = -5.5  # the rerank threshold the first Phase 6 pipeline shipped with
PACE_SECONDS = 1.0  # one live embedding per second stays under the 90 requests/minute client limit


def pct(xs: list[float], p: float) -> float:
    return float(np.percentile(xs, p)) if xs else float("nan")


def old_outcome(item: dict, prefix: str) -> dict:
    """The first pipeline: every candidate scored by MiniLM, best first, chunk threshold on the score."""
    ranked = sorted(item["ranked"], key=lambda c: -(c.rerank_score if c.rerank_score is not None else -math.inf))
    kept = select_context(ranked, OLD_THRESHOLD, settings.retrieval_final_top_k, settings.retrieval_context_tokens)
    files = list(dict.fromkeys(_norm(c.file_path, prefix) for c in kept))
    out = {"files": files, "context": bool(files), "chunks": len(kept)}
    if item["kind"] == "answerable":
        exp, ok = set(item["expected"]), set(item["expected"]) | set(item["acceptable"])
        out["hit"] = any(f in ok for f in files)
        out["recall"] = len(set(files) & exp) / len(exp)
        ranks = [i for i, f in enumerate(files, 1) if f in ok]
        out["mrr"] = 1 / ranks[0] if ranks else 0.0
        out["share"] = _mean([_norm(c.file_path, prefix) in ok for c in kept]) if kept else float("nan")
    return out


def summarize(rows: list[dict], key: str) -> dict:
    ans = [r[key] for r in rows if r["kind"] == "answerable"]
    off = [r[key] for r in rows if r["kind"] == "offtopic"]
    return {
        "hit": _mean([o["hit"] for o in ans]), "recall": _mean([o["recall"] for o in ans]),
        "mrr": _mean([o["mrr"] for o in ans]), "refused": _mean([not o["context"] for o in off]),
        "share": _mean([o["share"] for o in ans if o["context"]]),
        "chunks": _mean([o["chunks"] for o in ans if o["context"]]),
        "n_ans": len(ans), "n_off": len(off),
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT))
    ap.add_argument("--json", default=str(EVAL_DIR / ".cache" / "holdout_result.json"))
    ap.add_argument("--bootstrap", type=int, default=2000)
    args = ap.parse_args()

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    frozen = data.get("frozen", {})
    mine = {"gate_vector_threshold": settings.gate_vector_threshold, "relative_cutoff": settings.relative_cutoff,
            "bm25_protect": settings.bm25_protect, "rerank_top_n": settings.rerank_top_n}
    drift = {k: (frozen[k], v) for k, v in mine.items() if k in frozen and frozen[k] != v}
    if drift:
        print(f"REFUSING TO RUN: settings differ from the frozen ones (frozen, configured): {drift}")
        return 1
    print(f"frozen settings confirmed: {mine}")

    questions = data["questions"]
    prefix, exclude = data.get("path_prefix", ""), set(data.get("exclude", []))
    timed = TimedReranker(CrossEncoderReranker(settings.reranker_model))
    set_reranker(timed)
    embedder = CachingEmbedder(get_embedding_provider())
    set_embedding_provider(embedder)
    pid, name = await _find_project(data["existing_project"])

    # 1. embed every new question live, timed (paced, so the client rate limiter never adds a wait)
    todo = [q["q"] for q in questions if not embedder.cached(q["q"])]
    embed_ms: dict[str, float] = {}
    print(f"{len(questions)} questions; {len(questions) - len(todo)} cached, {len(todo)} live embeddings (~{len(todo)} Gemini calls)")
    for n, text in enumerate(todo, 1):
        start = time.perf_counter()
        try:
            await embedder.embed_query(text)
        except EmbeddingError as exc:
            print(f"\nSTOPPED after {n - 1}/{len(todo)} embeddings: {str(exc)[:160]}\nCached ones are kept; re-run later to resume.")
            return 2
        embed_ms[text] = 1000 * (time.perf_counter() - start)
        await asyncio.sleep(PACE_SECONDS)
    if embed_ms:
        print(f"live query embedding: p50 {pct(list(embed_ms.values()), 50):.0f} ms, p95 {pct(list(embed_ms.values()), 95):.0f} ms "
              f"(n={len(embed_ms)})")

    rows = []
    async with SessionLocal() as session:
        await pipeline.run(session, pid, questions[0]["q"])  # warm connections and the ONNX model; not measured
        timed.durations.clear(), timed.sizes.clear()
        # 2. production pass: settings as configured, reranker off. Measures search + Postgres time.
        prod = {}
        for q in questions:
            start = time.perf_counter()
            trace = await pipeline.run(session, pid, q["q"])
            prod[q["q"]] = (1000 * (time.perf_counter() - start), trace)
        # 3. record pass: gate and filter off, reranker on every candidate (for the old pipeline and the replay)
        for i, q in enumerate(questions, 1):
            before = len(timed.durations)
            trace = await pipeline.run(
                session, pid, q["q"], gate_threshold=-math.inf, relative_cutoff=0, rerank_top_n=10 ** 6,
            )
            rerank_ms = 1000 * sum(timed.durations[before:])
            cands = by_rrf([c for c in trace.candidates if _norm(c.file_path, prefix) not in exclude])
            scores = [c.vector_score for c in cands if c.vector_score is not None]
            rows.append({**q, "ranked": cands, "top_vec": max(scores) if scores else None, "rerank_ms": rerank_ms})
            if i % 40 == 0:
                print(f"  recorded {i}/{len(questions)}")

    for r in rows:
        r["new"] = _outcome(r, settings.gate_vector_threshold, prefix, cutoff=settings.relative_cutoff)
        r["old"] = old_outcome(r, prefix)
        ms, trace = prod[r["q"]]
        r["search_ms"] = ms
        served = [_norm(c.file_path, prefix) for c in trace.final if _norm(c.file_path, prefix) not in exclude]
        r["prod_files"] = list(dict.fromkeys(served))
        r["agrees"] = r["prod_files"] == r["new"]["files"] or any(  # excluded files can shift the top-K slots
            _norm(c.file_path, prefix) in exclude for c in trace.final)

    scored = [r for r in rows if r["kind"] in ("answerable", "offtopic")]
    new, old = summarize(scored, "new"), summarize(scored, "old")
    print(f"\n{new['n_ans']} answerable + {new['n_off']} off-topic questions (frozen settings, no tuning on this set)\n")
    print(f"{'':<34} {'hit':>6} {'R@5':>6} {'MRR':>6} {'refused':>8} {'share':>6} {'chunks':>6}")
    for label, s in (("OLD (rerank all, thr -5.5)", old), ("NEW (gate .82, cut-off .04, bm25 2)", new)):
        print(f"{label:<34} {s['hit']:>6.3f} {s['recall']:>6.3f} {s['mrr']:>6.3f} {s['refused']:>8.3f} {s['share']:>6.3f} {s['chunks']:>6.2f}")
    agree = _mean([r["agrees"] for r in scored])
    print(f"\nreplay vs production pipeline serve the same files for {agree:.1%} of questions")

    # latency: per question = live embed + search/Postgres (+ old pipeline's rerank time)
    emb = [embed_ms[r["q"]] for r in scored if r["q"] in embed_ms]
    srch = [r["search_ms"] for r in scored]
    rer = [r["rerank_ms"] for r in scored if r["top_vec"] is not None]
    tot_new = [embed_ms[r["q"]] + r["search_ms"] for r in scored if r["q"] in embed_ms]
    tot_old = [embed_ms[r["q"]] + r["search_ms"] + r["rerank_ms"] for r in scored if r["q"] in embed_ms]
    print(f"\n{'latency (ms)':<44} {'p50':>7} {'p95':>7} {'mean':>7}")
    for label, xs in (("query embedding (live Gemini call)", emb), ("search + Postgres (BM25, kNN, RRF, filter)", srch),
                      ("MiniLM rerank of all candidates (old only)", rer), ("NEW end to end (embed + search)", tot_new),
                      ("OLD end to end (embed + search + rerank)", tot_old)):
        if xs:
            print(f"{label:<44} {pct(xs, 50):>7.0f} {pct(xs, 95):>7.0f} {statistics.fmean(xs):>7.0f}")

    # paired bootstrap, new minus old
    rng = random.Random(1)
    qa = [r for r in scored if r["kind"] == "answerable"]
    qo = [r for r in scored if r["kind"] == "offtopic"]
    d = defaultdict(list)
    for _ in range(args.bootstrap):
        sa = [rng.choice(qa) for _ in qa]
        so = [rng.choice(qo) for _ in qo]
        d["hit"].append(_mean([r["new"]["hit"] for r in sa]) - _mean([r["old"]["hit"] for r in sa]))
        d["refused"].append(_mean([not r["new"]["context"] for r in so]) - _mean([not r["old"]["context"] for r in so]))
    print("\nNEW minus OLD, paired bootstrap (median [5%, 95%]):")
    for k in ("hit", "refused"):
        print(f"  {k:<8} {np.median(d[k]):+.3f} [{np.percentile(d[k], 5):+.3f}, {np.percentile(d[k], 95):+.3f}]")

    print("\nhit per tag (OLD -> NEW):")
    for t in sorted({r["tag"] for r in qa}):
        rs = [r for r in qa if r["tag"] == t]
        print(f"  {t:<10} n={len(rs):<3} {_mean([r['old']['hit'] for r in rs]):.2f} -> {_mean([r['new']['hit'] for r in rs]):.2f}")
    print("off-topic refused per tag (OLD -> NEW):")
    for t in sorted({r["tag"] for r in qo}):
        rs = [r for r in qo if r["tag"] == t]
        print(f"  {t:<12} n={len(rs):<3} {_mean([not r['old']['context'] for r in rs]):.2f} -> {_mean([not r['new']['context'] for r in rs]):.2f}")

    print("\n=== NEW pipeline failures ===")
    for r in scored:
        top = f"top similarity {r['top_vec']:.3f}" if r["top_vec"] is not None else "no vector hit"
        if r["kind"] == "answerable" and not r["new"]["hit"]:
            why = "GATE" if not r["new"]["context"] else "FILTER/ORDER"
            print(f"  MISS ({why}) [{r['tag']}] {r['q']}\n        expected {r['expected']}  served {r['new']['files']}  {top}")
        elif r["kind"] == "offtopic" and r["new"]["context"]:
            print(f"  LEAK [{r['tag']}] {r['q']}\n        served {r['new']['files']}  {top}")

    out = {"frozen": frozen, "new": new, "old": old, "agreement": agree,
           "latency_ms": {"embed": emb, "search": srch, "rerank_old": rer, "new_total": tot_new, "old_total": tot_old},
           "questions": [{k: r[k] for k in ("q", "kind", "tag", "expected", "top_vec", "new", "old", "search_ms", "rerank_ms")}
                         for r in rows]}
    Path(args.json).write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    print(f"\nsaved {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
