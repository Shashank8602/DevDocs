"""Three offline retrieval experiments: `python -m eval.experiments` from backend/.

Step 1 (slow, once): run every question through the real pipeline (cached query embeddings, local ES /
Postgres / reranker, no API calls) and record every candidate with its BM25 / vector ranks and scores and
its rerank score. Saved to eval/.cache/pipeline_records.json; `--reuse` skips this step.

Step 2 (fast): replay selection offline.
  1. weighted RRF       vector vs BM25 weight in the fusion, no reranker
  2. refusal signal     can the top vector similarity or BM25 score refuse off-topic questions as well as
                        the reranker score, so the reranker is not needed for refusal?
  3. rerank top N       rerank only the top N of the fusion instead of every candidate

Cross-encoder scores are per (query, passage) pair, so reranking a subset gives each passage the same
score it had inside the full batch; the subsets are therefore simulated exactly from the recorded scores.
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
from pathlib import Path

import numpy as np

from app.config import settings
from app.db import SessionLocal
from app.providers.embeddings import EmbeddingError, get_embedding_provider, set_embedding_provider
from app.retrieval import pipeline
from app.retrieval.reranker import CrossEncoderReranker, set_reranker
from app.retrieval.selection import Candidate, select as select_context
from eval.ablation import TimedReranker
from eval.run import CACHE_PATH, DEFAULT_DATASET, CachingEmbedder, _find_project, _norm

RECORDS = CACHE_PATH.with_name("pipeline_records.json")
TOP_K, BUDGET = settings.retrieval_final_top_k, settings.retrieval_context_tokens
INF = float("inf")


# ------------------------------------------------------------------ step 1: record
async def record(args) -> list[dict]:
    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    questions = data["questions"][: args.limit or None]
    prefix, exclude = data.get("path_prefix", ""), set(data.get("exclude", []))
    for q in questions:
        q.setdefault("acceptable", [])
        q.setdefault("kind", "answerable" if q["expected"] else "offtopic")
        q.setdefault("tag", "all")

    timed = TimedReranker(CrossEncoderReranker(settings.reranker_model))
    set_reranker(timed)
    embedder = CachingEmbedder(get_embedding_provider())
    set_embedding_provider(embedder)
    missing = [q["q"] for q in questions if not embedder.cached(q["q"])]
    if missing:
        raise SystemExit(f"{len(missing)} questions have no cached query embedding; run eval.run first (this never calls the API)")

    pid, name = await _find_project(args.project or data["existing_project"])
    records = []
    async with SessionLocal() as session:
        print(f"recording {len(questions)} questions on project {name!r}")
        for i, q in enumerate(questions, 1):
            try:
                trace = await pipeline.run(
                    session, pid, q["q"], gate_threshold=-INF, relative_cutoff=0, rerank_top_n=10 ** 6,
                )
            except (pipeline.RetrievalError, EmbeddingError) as exc:
                raise SystemExit(f"stopped at question {i}: {exc}")
            bm = {h.chunk_id: h.score for h in trace.bm25}
            vc = {h.chunk_id: h.score for h in trace.vector}
            cands = []
            for c in trace.candidates:
                path = _norm(c.file_path, prefix)
                if path in exclude:
                    continue
                cands.append({
                    "chunk_id": c.chunk_id, "file_id": c.file_id, "file": path, "line_start": c.line_start,
                    "line_end": c.line_end, "page": c.page_number, "tokens": c.token_count, "rrf": c.rrf_score,
                    "bm25_rank": c.bm25_rank, "vector_rank": c.vector_rank, "rerank": c.rerank_score,
                    "bm25_score": bm.get(c.chunk_id), "vector_score": vc.get(c.chunk_id),
                })
            records.append({k: q[k] for k in ("q", "kind", "tag", "expected", "acceptable")} | {"cands": cands})
            if i % 50 == 0:
                print(f"  recorded {i}/{len(questions)}")
    d = timed.durations[1:] or timed.durations  # the first call also loads the model
    n = timed.sizes[1:] or timed.sizes
    out = {"records": records, "ms_per_passage": 1000 * sum(d) / max(1, sum(n)), "ms_per_question": 1000 * statistics.fmean(d)}
    RECORDS.parent.mkdir(parents=True, exist_ok=True)
    RECORDS.write_text(json.dumps(out), encoding="utf-8")
    print(f"saved {RECORDS}")
    return out


# ------------------------------------------------------------------ step 2: replay
def to_candidates(rec: dict) -> list[Candidate]:
    return [
        Candidate(c["chunk_id"], c["file_id"], c["file"], "", c["line_start"], c["line_end"], c["page"], None,
                  c["tokens"], c["rrf"], c["bm25_rank"], c["vector_rank"], rerank_score=c["rerank"])
        for c in rec["cands"]
    ]


def by_rrf(cands, w_bm25=1.0, w_vec=1.0, k=60):
    def score(c):
        return ((w_bm25 / (k + c.bm25_rank)) if c.bm25_rank is not None else 0.0) + \
               ((w_vec / (k + c.vector_rank)) if c.vector_rank is not None else 0.0)
    return sorted(cands, key=lambda c: (-score(c), c.bm25_rank if c.bm25_rank is not None else 10 ** 9,
                                        c.vector_rank if c.vector_rank is not None else 10 ** 9))


def by_rerank(cands):
    return sorted(cands, key=lambda c: -c.rerank_score)


def rerank_top_n(cands, base, n):
    """Only the first n of `base` are scored; they are re-ordered by score, the rest follow unscored."""
    head, tail = base[:n], base[n:]
    out = [Candidate(**{**c.__dict__}) for c in head]
    out = by_rerank(out)
    for c in tail:  # never scored: an absolute threshold must drop them, an order-only stage keeps them
        out.append(Candidate(**{**c.__dict__, "rerank_score": -INF}))
    return out


def judge(rec: dict, ordered: list[Candidate], threshold: float = -INF) -> dict:
    kept = select_context(ordered, threshold, TOP_K, BUDGET)
    files = list(dict.fromkeys(c.file_path for c in kept))
    out = {"context": bool(files)}
    if rec["kind"] == "answerable":
        exp, ok = set(rec["expected"]), set(rec["expected"]) | set(rec["acceptable"])
        out["hit"] = any(f in ok for f in files)
        out["recall"] = len(set(files) & exp) / len(exp)
        r = [i for i, f in enumerate(files, 1) if f in ok]
        out["mrr"] = 1 / r[0] if r else 0.0
    return out


def mean(xs):
    xs = list(xs)
    return statistics.fmean(xs) if xs else float("nan")


def auc(pos: list[float], neg: list[float]) -> float:
    """P(score of a random answerable question > score of a random off-topic one)."""
    if not pos or not neg:
        return float("nan")
    p, n = np.array(pos), np.array(neg)
    return float(((p[:, None] > n[None, :]).sum() + 0.5 * (p[:, None] == n[None, :]).sum()) / (len(p) * len(n)))


def split(recs: list[dict], seed: int = 7, holdout: float = 0.3):
    ordered = sorted(recs, key=lambda r: r["q"])
    random.Random(seed).shuffle(ordered)
    n = int(len(ordered) * holdout)
    return ordered[n:], ordered[:n]


def best_j(points: list[tuple[float, float, float]]):
    """points: (threshold, hit, refused). Returns the point with the best J (ties: the middle one)."""
    top = max(0.5 * h + 0.5 * r for _, h, r in points)
    tied = [p for p in points if abs(0.5 * p[1] + 0.5 * p[2] - top) < 1e-9]
    return tied[len(tied) // 2], top


def main_sync(args, blob: dict) -> None:
    recs = blob["records"]
    for r in recs:
        r["_c"] = to_candidates(r)
    scored = [r for r in recs if r["kind"] in ("answerable", "offtopic")]
    ans = [r for r in scored if r["kind"] == "answerable"]
    off = [r for r in scored if r["kind"] == "offtopic"]
    tune, held = split(scored)
    print(f"\n{len(ans)} answerable + {len(off)} off-topic questions "
          f"({len(tune)} tune / {len(held)} held-out); mean pool {mean(len(r['_c']) for r in scored):.1f} chunks; "
          f"reranker {blob['ms_per_passage']:.0f} ms per passage, {blob['ms_per_question']:.0f} ms per question")

    result: dict = {}

    # ============================ experiment 1: weighted RRF
    print("\n=== 1. weighted RRF (no reranker; vector weight fixed at 1.0) ===")
    print(f"{'BM25 weight':<22} {'hit':>6} {'Recall@5':>9} {'MRR':>6} | held-out Recall@5")
    base_out = {id(r): judge(r, by_rrf(r["_c"])) for r in ans}
    rows1 = {}
    hold_ids = {id(r) for r in held}
    for w in (1.0, 0.75, 0.5, 0.33, 0.25, 0.1, 0.0):
        o = {id(r): judge(r, by_rrf(r["_c"], w_bm25=w)) for r in ans}
        rows1[w] = o
        hold = mean(o[id(r)]["recall"] for r in ans if id(r) in hold_ids)
        tag = "  (current: equal weights)" if w == 1.0 else ("  (vector only)" if w == 0.0 else "")
        print(f"{w:<22} {mean(v['hit'] for v in o.values()):>6.3f} {mean(v['recall'] for v in o.values()):>9.3f} "
              f"{mean(v['mrr'] for v in o.values()):>6.3f} | {hold:>6.3f}{tag}")
    best_w = max(rows1, key=lambda w: (mean(v["recall"] for v in rows1[w].values()), mean(v["hit"] for v in rows1[w].values())))
    up = sum(rows1[best_w][id(r)]["recall"] > base_out[id(r)]["recall"] for r in ans)
    down = sum(rows1[best_w][id(r)]["recall"] < base_out[id(r)]["recall"] for r in ans)
    print(f"best BM25 weight by Recall@5: {best_w}; vs equal weights: better on {up}, worse on {down}, same on {len(ans) - up - down}")
    # also vary k with equal weights
    for k in (10, 20, 100):
        o = [judge(r, by_rrf(r["_c"], k=k)) for r in ans]
        print(f"   equal weights, k={k:<3}  hit {mean(v['hit'] for v in o):.3f}  Recall@5 {mean(v['recall'] for v in o):.3f}  "
              f"MRR {mean(v['mrr'] for v in o):.3f}")
    tags = sorted({r["tag"] for r in ans})
    print(f"\n{'tag':<12} {'n':>3} {'RRF 1.0':>8} {f'w={best_w}':>8} {'vector':>8}")
    for t in tags:
        rs = [r for r in ans if r["tag"] == t]
        a = mean(rows1[1.0][id(r)]["recall"] for r in rs)
        b = mean(rows1[best_w][id(r)]["recall"] for r in rs)
        v = mean(rows1[0.0][id(r)]["recall"] for r in rs)
        print(f"{t:<12} {len(rs):>3} {a:>8.2f} {b:>8.2f} {v:>8.2f}")
    result["exp1"] = {str(w): {"hit": mean(v["hit"] for v in o.values()), "recall": mean(v["recall"] for v in o.values()),
                               "mrr": mean(v["mrr"] for v in o.values())} for w, o in rows1.items()}
    result["exp1"]["best_w"] = best_w

    # ============================ experiment 2: refusal signal
    print("\n=== 2. which score separates answerable from off-topic questions? ===")

    def sig(r, name):
        cs = r["cands"]
        vals = [c[name] for c in cs if c[name] is not None]
        return max(vals) if vals else -INF

    sigs = {"rerank": "rerank", "vector": "vector_score", "bm25": "bm25_score"}
    has_hit = [r for r in ans if judge(r, by_rrf(r["_c"], w_bm25=best_w))["hit"]]
    print(f"{'signal (question-level top score)':<36} {'AUC all':>8} {'AUC findable':>13}   score range answerable / off-topic")
    for label, key in sigs.items():
        a = [sig(r, key) for r in ans]
        o = [sig(r, key) for r in off]
        f = [sig(r, key) for r in has_hit]
        print(f"top {label:<32} {auc(a, o):>8.3f} {auc(f, o):>13.3f}   "
              f"{np.percentile(a, 10):.2f}..{np.percentile(a, 90):.2f} / {np.percentile(o, 10):.2f}..{np.percentile(o, 90):.2f}")
    print("AUC 1.0 = perfect separation, 0.5 = no better than chance; 'findable' = answerable questions whose "
          "ordered top 5 contains a relevant file.")

    # strategies: (label, ordering fn, gate)
    def gated(label, order_fn, key):
        """Question-level gate: refuse if the question's top `key` score is below t, else serve the top 5."""
        grid = sorted({round(sig(r, key), 3) for r in scored if sig(r, key) > -INF})
        grid = np.round(np.quantile(grid, np.linspace(0, 1, 160)), 3).tolist() + [-INF]
        base = {id(r): judge(r, order_fn(r["_c"])) for r in scored}

        def at(t, rs):
            h = [base[id(r)]["hit"] and sig(r, key) >= t for r in rs if r["kind"] == "answerable"]
            f = [not (sig(r, key) >= t) for r in rs if r["kind"] == "offtopic"]
            return mean(h), mean(f)
        return {"label": label, "grid": sorted(set(grid)), "at": at, "base": base}

    def chunk_level(label, order_fn):
        """The shipped behaviour: drop chunks scoring below t on the reranker."""
        grid = sorted({round(c["rerank"], 2) for r in scored for c in r["cands"]})
        grid = np.round(np.quantile(grid, np.linspace(0, 1, 200)), 2).tolist()
        cache: dict = {}

        def at(t, rs):
            h, f = [], []
            for r in rs:
                key = (id(r), t)
                if key not in cache:
                    cache[key] = judge(r, order_fn(r["_c"]), t)
                o = cache[key]
                if r["kind"] == "answerable":
                    h.append(o["hit"])
                else:
                    f.append(not o["context"])
            return mean(h), mean(f)
        return {"label": label, "grid": sorted(set(grid)), "at": at}

    strategies = [
        chunk_level("A  current: rerank order, chunk threshold", by_rerank),
        gated("B  rerank score gate, RRF order", lambda c: by_rrf(c), "rerank"),
        gated(f"C  vector similarity gate, RRF(w={best_w}) order", lambda c: by_rrf(c, w_bm25=best_w), "vector_score"),
        gated("D  BM25 score gate, RRF order", lambda c: by_rrf(c), "bm25_score"),
        gated("E  vector similarity gate, vector order", lambda c: by_rrf(c, w_bm25=0.0), "vector_score"),
    ]
    print(f"\n{'strategy':<50} {'best J':>7} {'thr':>8} {'hit':>6} {'refused':>8} | held-out hit / refused (t from tune)")
    exp2 = {}
    for s in strategies:
        pts = [(t, *s["at"](t, scored)) for t in s["grid"]]
        (t, h, f), j = best_j(pts)
        tp = [(t_, *s["at"](t_, tune)) for t_ in s["grid"]]
        (t_tune, _, _), _ = best_j(tp)
        hh, hf = s["at"](t_tune, held)
        print(f"{s['label']:<50} {j:>7.3f} {t:>8.2f} {h:>6.3f} {f:>8.3f} | {hh:.3f} / {hf:.3f}")
        exp2[s["label"]] = {"best_j": j, "threshold": t, "hit": h, "refused": f, "holdout_hit": hh, "holdout_refused": hf}
        s["pts"] = pts

    print("\nhit rate when each strategy is tuned to a given off-topic refusal rate (higher is better):")
    print(f"{'strategy':<50} {'>=80%':>7} {'>=87.5%':>8} {'>=90%':>7} {'>=95%':>7}")
    for s in strategies:
        cells = []
        for level in (0.80, 0.875, 0.90, 0.95):
            ok = [(h, t) for t, h, f in s["pts"] if f >= level]
            cells.append(f"{max(ok)[0]:.3f}" if ok else "  -  ")
        print(f"{s['label']:<50} {cells[0]:>7} {cells[1]:>8} {cells[2]:>7} {cells[3]:>7}")
        exp2[s["label"]]["hit_at_refusal"] = dict(zip(("0.80", "0.875", "0.90", "0.95"), cells))
    result["exp2"] = exp2

    # ============================ experiment 3: rerank only the top N
    print("\n=== 3. rerank only the top N candidates of the RRF order ===")
    per_passage = blob["ms_per_passage"]
    base_order = lambda c: by_rrf(c)  # noqa: E731
    print(f"{'candidates reranked':<22} {'est ms':>7} {'best J':>7} {'thr':>7} {'hit':>6} {'Recall@5':>9} {'MRR':>6} {'refused':>8}")
    exp3 = {}
    for n in (5, 8, 10, 12, 20):
        order = lambda c, n=n: rerank_top_n(c, base_order(c), n)  # noqa: E731
        grid = [round(x, 1) for x in np.arange(-12.0, 4.0, 0.25)]
        cache = {}

        def at(t, rs, order=order, cache=cache):
            hs, fs = [], []
            for r in rs:
                key = (id(r), t)
                if key not in cache:
                    cache[key] = judge(r, order(r["_c"]), t)
                o = cache[key]
                if r["kind"] == "answerable":
                    hs.append(o["hit"])
                else:
                    fs.append(not o["context"])
            return mean(hs), mean(fs)
        pts = [(t, *at(t, scored)) for t in grid]
        (t, h, f), j = best_j(pts)
        outs = [judge(r, order(r["_c"]), t) for r in ans]
        est = min(n, mean(len(r["_c"]) for r in scored)) * per_passage
        label = f"top {n}" if n < 20 else "all (current)"
        print(f"{label:<22} {est:>7.0f} {j:>7.3f} {t:>7.2f} {h:>6.3f} {mean(o['recall'] for o in outs):>9.3f} "
              f"{mean(o['mrr'] for o in outs):>6.3f} {f:>8.3f}")
        exp3[label] = {"est_ms": est, "best_j": j, "threshold": t, "hit": h, "refused": f,
                       "recall": mean(o["recall"] for o in outs), "mrr": mean(o["mrr"] for o in outs)}
    result["exp3"] = exp3

    # combined: weighted RRF candidate order + rerank top N
    print("\n=== combination: weighted RRF picks the candidates, reranker orders and gates them ===")
    for n in (8, 20):
        order = lambda c, n=n: rerank_top_n(c, by_rrf(c, w_bm25=best_w), n)  # noqa: E731
        pts = []
        for t in [round(x, 1) for x in np.arange(-12.0, 4.0, 0.25)]:
            hs = [judge(r, order(r["_c"]), t)["hit"] for r in ans]
            fs = [not judge(r, order(r["_c"]), t)["context"] for r in off]
            pts.append((t, mean(hs), mean(fs)))
        (t, h, f), j = best_j(pts)
        outs = [judge(r, order(r["_c"]), t) for r in ans]
        print(f"w_bm25={best_w}, rerank top {n:<2}: best J {j:.3f} at t={t:.2f}  hit {h:.3f}  "
              f"Recall@5 {mean(o['recall'] for o in outs):.3f}  refused {f:.3f}")
    Path(args.json).write_text(json.dumps(result, indent=1), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET.with_name("dataset_devdocs.json")))
    ap.add_argument("--project")
    ap.add_argument("--reuse", action="store_true", help="use the saved pipeline records")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", default=str(RECORDS.with_name("experiments_result.json")))
    args = ap.parse_args()
    if args.reuse and RECORDS.exists():
        blob = json.loads(RECORDS.read_text(encoding="utf-8"))
    else:
        blob = asyncio.run(record(args))
    main_sync(args, blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
