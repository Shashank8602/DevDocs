"""Per-stage ablation of the retrieval pipeline: `python -m eval.ablation` from backend/.

Every question's candidate pool is re-ordered and re-cut the way each stage of the pipeline would have, and
every stage goes through the SAME post-processing (overlap cleanup, Top-K 5, token budget), so the stages are
directly comparable:

  bm25         candidates the keyword search returned, in BM25 order
  vector       candidates the kNN search returned, in vector order
  rrf          both lists fused by Reciprocal Rank Fusion (k=60)
  rrf+rerank   reranked candidates, no cut (the reranker the first pipeline used)
  first        reranked + chunk threshold -5.5: the FIRST Phase 6 pipeline, kept as the baseline
  rrf+gate     RRF order + the vector-similarity gate (question refused below the gate threshold)
  shipped      rrf+gate + the relative filter (cut-off, BM25 top N protected): what chat receives today

Only the last three can refuse a question.

Two ways to get the candidate pool (no API calls either way, query embeddings must be cached):
  default      run every question through the real pipeline (Postgres + ES + the local reranker, slow)
  --reuse P    read the records written by `eval.experiments` (or by --save), seconds
--save P writes the pool of a live run so the next run can use --reuse.

Options: --dataset PATH  --project NAME  --reuse PATH  --save PATH  --json PATH  --limit N
"""
import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import File
from app.providers.embeddings import EmbeddingError, get_embedding_provider, set_embedding_provider
from app.retrieval import pipeline
from app.retrieval.reranker import CrossEncoderReranker, set_reranker
from app.retrieval.selection import Candidate, select as select_context
from eval.run import DEFAULT_DATASET, CachingEmbedder, _find_project, _norm
from eval.run import _outcome as gate_outcome

HISTORICAL_THRESHOLD = -5.5  # the rerank threshold the first Phase 6 pipeline shipped with
STAGES = ["bm25", "vector", "rrf", "rrf+rerank", "first", "rrf+gate", "shipped"]
CAN_REFUSE = ("first", "rrf+gate", "shipped")
LABEL = {
    "bm25": "BM25 only", "vector": "vector only", "rrf": "BM25 + vector (RRF)", "rrf+rerank": "RRF + reranker (no cut)",
    "first": f"first pipeline: reranker + threshold {HISTORICAL_THRESHOLD}",
    "rrf+gate": f"RRF + vector gate {settings.gate_vector_threshold}",
    "shipped": f"shipped: + cut-off {settings.relative_cutoff}, BM25 top {settings.bm25_protect}",
}
INF = 10 ** 9


class TimedReranker:
    """Wraps the real reranker and records how long each scoring call took (one call per question)."""

    def __init__(self, inner):
        self.inner, self.name, self.durations, self.sizes = inner, inner.name, [], []

    async def score(self, query, passages):
        start = time.perf_counter()
        try:
            return await self.inner.score(query, passages)
        finally:
            self.durations.append(time.perf_counter() - start)
            self.sizes.append(len(passages))


def _mean(xs) -> float:
    xs = [x for x in xs if x == x]  # ignore NaN
    return statistics.fmean(xs) if xs else float("nan")


def _grade(item: dict, files: list[str], kept: list, prefix: str) -> dict:
    out = {"files": files, "context": bool(files), "chunks": len(kept)}
    if item["kind"] == "answerable":
        exp, ok = set(item["expected"]), set(item["expected"]) | set(item["acceptable"])
        out["hit"] = any(f in ok for f in files)
        out["recall"] = len(set(files) & exp) / len(exp)
        ranks = [i for i, f in enumerate(files, 1) if f in ok]
        out["mrr"] = 1 / ranks[0] if ranks else 0.0
        out["share"] = _mean([_norm(c.file_path, prefix) in ok for c in kept]) if kept else float("nan")
    return out


def threshold_outcome(item: dict, threshold: float, prefix: str) -> dict:
    """Rerank-score threshold -> overlap -> budget -> Top-K (the first pipeline, and the un-cut earlier stages)."""
    kept = select_context(item["ranked"], threshold, settings.retrieval_final_top_k, settings.retrieval_context_tokens)
    return _grade(item, list(dict.fromkeys(_norm(c.file_path, prefix) for c in kept)), kept, prefix)


def stage_orders(ranked: list) -> dict[str, list]:
    """The candidate pool re-ordered the way each stage would have ranked it."""
    rrf = sorted(ranked, key=lambda c: (-c.rrf_score, c.bm25_rank if c.bm25_rank is not None else INF,
                                        c.vector_rank if c.vector_rank is not None else INF))
    return {
        "bm25": sorted((c for c in ranked if c.bm25_rank is not None), key=lambda c: c.bm25_rank),
        "vector": sorted((c for c in ranked if c.vector_rank is not None), key=lambda c: c.vector_rank),
        "rrf": rrf,
        "rrf+rerank": sorted(ranked, key=lambda c: -(c.rerank_score if c.rerank_score is not None else -INF)),
    }


def stage_outcome(r: dict, stage: str, prefix: str) -> dict:
    if stage in ("bm25", "vector", "rrf", "rrf+rerank"):
        return threshold_outcome({**r, "ranked": r["orders"][stage]}, -math.inf, prefix)
    if stage == "first":
        return threshold_outcome({**r, "ranked": r["orders"]["rrf+rerank"]}, HISTORICAL_THRESHOLD, prefix)
    cutoff = 0 if stage == "rrf+gate" else settings.relative_cutoff
    return gate_outcome({**r, "ranked": r["orders"]["rrf"]}, settings.gate_vector_threshold, prefix, cutoff=cutoff)


def rows_from_records(blob: dict, prefix: str, exclude: set[str]) -> list[dict]:
    """Rebuild each question's candidate pool from `eval.experiments` / --save records."""
    rows = []
    for rec in blob["records"]:
        cands = []
        for c in rec["cands"]:
            if _norm(c["file"], prefix) in exclude:
                continue
            cand = Candidate(c["chunk_id"], c["file_id"], c["file"], "", c["line_start"], c["line_end"], c["page"],
                             None, c["tokens"], c["rrf"], c["bm25_rank"], c["vector_rank"], rerank_score=c["rerank"])
            cand.vector_score = c.get("vector_score")
            cands.append(cand)
        rows.append({k: rec[k] for k in ("q", "kind", "tag", "expected", "acceptable")} | {"ranked": cands})
    return rows


def records_from_rows(rows: list[dict], timing: dict) -> dict:
    out = []
    for r in rows:
        cands = [{"chunk_id": c.chunk_id, "file_id": c.file_id, "file": c.file_path, "line_start": c.line_start,
                  "line_end": c.line_end, "page": c.page_number, "tokens": c.token_count, "rrf": c.rrf_score,
                  "bm25_rank": c.bm25_rank, "vector_rank": c.vector_rank, "rerank": c.rerank_score,
                  "vector_score": c.vector_score} for c in r["ranked"]]
        out.append({k: r[k] for k in ("q", "kind", "tag", "expected", "acceptable")} | {"cands": cands})
    return {"records": out, **timing}


async def rows_live(args, questions: list[dict], prefix: str, exclude: set[str]):
    reranker = TimedReranker(CrossEncoderReranker(settings.reranker_model))
    set_reranker(reranker)
    embedder = CachingEmbedder(get_embedding_provider())
    set_embedding_provider(embedder)
    uncached = [q["q"] for q in questions if not embedder.cached(q["q"])]
    if uncached:
        print(f"{len(uncached)} questions have no cached query embedding; this script never calls the API.")
        print("Run `python -m eval.run --dataset ...` first, or drop them with --limit.")
        return None
    existing = args.project or json.loads(Path(args.dataset).read_text(encoding="utf-8")).get("existing_project")
    if not existing:
        print("the dataset needs an existing_project (this script does not ingest)")
        return None
    pid, name = await _find_project(existing)
    rows = []
    async with SessionLocal() as session:
        n_ready = len((await session.scalars(select(File.id).where(File.project_id == pid, File.status == "ready"))).all())
        print(f"project {name!r} {pid}: {n_ready} ready files, reranker {settings.reranker_model}, {len(questions)} questions")
        for i, q in enumerate(questions, 1):
            try:
                trace = await pipeline.run(
                    session, pid, q["q"], gate_threshold=-math.inf, relative_cutoff=0, rerank_top_n=10 ** 6,
                )
            except (pipeline.RetrievalError, EmbeddingError) as exc:
                print(f"STOPPED at question {i}: {exc}")
                return None
            ranked = [c for c in trace.candidates if _norm(c.file_path, prefix) not in exclude]
            rows.append({**q, "ranked": ranked})
            if i % 50 == 0:
                print(f"  ran {i}/{len(questions)}")
    d = reranker.durations[1:] or reranker.durations  # the first call also loads the model
    n = reranker.sizes[1:] or reranker.sizes
    timing = {"ms_per_passage": 1000 * sum(d) / max(1, sum(n)), "ms_per_question": 1000 * statistics.fmean(d),
              "ms_per_question_p50": 1000 * statistics.median(d),
              "ms_per_question_p95": 1000 * sorted(d)[min(len(d) - 1, int(0.95 * len(d)))]}
    return rows, timing


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET.with_name("dataset_devdocs.json")))
    ap.add_argument("--project")
    ap.add_argument("--reuse", help="read candidate pools from this records file instead of running the pipeline")
    ap.add_argument("--save", help="write the candidate pools of a live run here")
    ap.add_argument("--json", help="write raw results here")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    questions = data["questions"][: args.limit or None]
    prefix, exclude = data.get("path_prefix", ""), set(data.get("exclude", []))
    for q in questions:
        q.setdefault("acceptable", [])
        q.setdefault("kind", "answerable" if q["expected"] else "offtopic")
        q.setdefault("tag", "all")

    if args.reuse:
        blob = json.loads(Path(args.reuse).read_text(encoding="utf-8"))
        rows, timing = rows_from_records(blob, prefix, exclude), {k: v for k, v in blob.items() if k != "records"}
        print(f"{len(rows)} questions from {args.reuse} (no pipeline run)")
    else:
        got = await rows_live(args, questions, prefix, exclude)
        if got is None:
            return 2
        rows, timing = got
        if args.save:
            Path(args.save).write_text(json.dumps(records_from_rows(rows, timing)), encoding="utf-8")
            print(f"saved candidate pools to {args.save}")

    for r in rows:
        r["orders"] = stage_orders(r["ranked"])
        scores = [c.vector_score for c in r["ranked"] if c.vector_score is not None]
        r["top_vec"] = max(scores) if scores else None
        r["pool"] = len(r["ranked"])

    scored = [r for r in rows if r["kind"] in ("answerable", "offtopic")]
    ans = [r for r in scored if r["kind"] == "answerable"]
    off = [r for r in scored if r["kind"] == "offtopic"]
    res = {s: {id(r): stage_outcome(r, s, prefix) for r in scored} for s in STAGES}
    print(f"gate {settings.gate_vector_threshold}, cut-off {settings.relative_cutoff}, BM25 protect {settings.bm25_protect}")

    # ---- table 1
    print(f"\n=== per-stage quality: {len(ans)} answerable + {len(off)} off-topic questions ===")
    print(f"{'stage':<46} {'hit':>6} {'Recall@5':>9} {'MRR':>6} {'refused':>8} {'share':>6} {'chunks':>7}")
    summary = {}
    for s in STAGES:
        g = lambda k: _mean([res[s][id(r)][k] for r in ans])  # noqa: E731
        served = [res[s][id(r)] for r in ans if res[s][id(r)]["context"]]
        refused = _mean([not res[s][id(r)]["context"] for r in off]) if s in CAN_REFUSE else float("nan")
        summary[s] = {"hit": g("hit"), "recall": g("recall"), "mrr": g("mrr"), "refused": refused,
                      "share": _mean([o["share"] for o in served]), "chunks": _mean([o["chunks"] for o in served])}
        v = summary[s]
        ref = f"{v['refused']:>8.3f}" if s in CAN_REFUSE else f"{'-':>8}"
        print(f"{LABEL[s]:<46} {v['hit']:>6.3f} {v['recall']:>9.3f} {v['mrr']:>6.3f} {ref} {v['share']:>6.3f} {v['chunks']:>7.2f}")
    print("('-' = the stage has no relevance score, so it cannot refuse; share = served chunks from an expected or "
          "acceptable file, over answered questions)")

    # ---- table 2: deltas
    print("\n=== what each step adds ===")
    best_leg = max(("bm25", "vector"), key=lambda s: summary[s]["recall"])
    steps = [(f"RRF over the better single leg ({best_leg})", best_leg, "rrf"),
             ("BM25 over vector", "vector", "bm25"),
             ("reranker over RRF (no cut)", "rrf", "rrf+rerank"),
             ("first pipeline's threshold over no cut", "rrf+rerank", "first"),
             ("vector gate over plain RRF", "rrf", "rrf+gate"),
             ("relative filter + BM25 protect over the gate", "rrf+gate", "shipped"),
             ("shipped over the first pipeline", "first", "shipped")]
    for label, a, b in steps:
        print(f"{label:<48} hit {summary[b]['hit'] - summary[a]['hit']:+.3f}   "
              f"Recall@5 {summary[b]['recall'] - summary[a]['recall']:+.3f}   MRR {summary[b]['mrr'] - summary[a]['mrr']:+.3f}   "
              f"share {summary[b]['share'] - summary[a]['share']:+.3f}")

    # ---- table 3: by tag
    print("\n=== Recall@5 by question type ===")
    cols = ("bm25", "vector", "rrf", "first", "shipped")
    print(f"{'tag':<12} {'n':>3} {'BM25':>6} {'vector':>7} {'RRF':>6} {'first':>6} {'shipped':>8} {'shipped-first':>14}")
    by_tag = defaultdict(list)
    for r in ans:
        by_tag[r["tag"]].append(r)
    for tag, rs in sorted(by_tag.items()):
        v = {s: _mean([res[s][id(r)]["recall"] for r in rs]) for s in cols}
        print(f"{tag:<12} {len(rs):>3} {v['bm25']:>6.2f} {v['vector']:>7.2f} {v['rrf']:>6.2f} {v['first']:>6.2f} "
              f"{v['shipped']:>8.2f} {v['shipped'] - v['first']:>+14.2f}")
    off_tags = defaultdict(list)
    for r in off:
        off_tags[r["tag"]].append(r)
    print("\noff-topic refused by type:      " + "   ".join(f"{t} (n={len(rs)})" for t, rs in sorted(off_tags.items())))
    for s in CAN_REFUSE:
        print(f"  {s:<10}" + "   ".join(
            f"{_mean([not res[s][id(r)]['context'] for r in rs]):>{len(t) + len(str(len(rs))) + 5}.2f}" for t, rs in sorted(off_tags.items())))

    # ---- table 4: question by question
    def tally(key: str, a: str, b: str):
        up = sum(1 for r in ans if res[b][id(r)][key] > res[a][id(r)][key])
        down = sum(1 for r in ans if res[b][id(r)][key] < res[a][id(r)][key])
        return up, down, len(ans) - up - down

    for a, b, title in (("rrf", "rrf+rerank", "reranker vs RRF"), ("first", "shipped", "shipped vs first pipeline")):
        print(f"\n=== {title}, question by question ===")
        for key, name_ in (("hit", "hit"), ("recall", "Recall@5"), ("mrr", "MRR")):
            up, down, same = tally(key, a, b)
            print(f"{name_:<9} better: {up:>3}   worse: {down:>3}   unchanged: {same:>3}")

    # ---- candidate pool / ceiling
    pool_hit = _mean([any(_norm(c.file_path, prefix) in set(r["expected"]) | set(r["acceptable"]) for c in r["ranked"])
                      for r in ans])
    print(f"\ncandidate pool: mean {_mean([r['pool'] for r in scored]):.1f} chunks per question")
    print(f"ceiling: a relevant file is somewhere in the pool for {pool_hit:.3f} of answerable questions "
          f"(no reordering or cut of this pool can beat that)")

    # ---- latency (reranker timing of the live or recorded run; the shipped pipeline does not rerank)
    if "ms_per_question" in timing:
        p50 = timing.get("ms_per_question_p50")
        print(f"\nreranker time per question when it scores every candidate (first pipeline): mean "
              f"{timing['ms_per_question']:.0f} ms" + (f", p50 {p50:.0f} ms" if p50 else "") +
              f", {timing['ms_per_passage']:.0f} ms per passage. The shipped pipeline does not call it.")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "n_answerable": len(ans), "n_offtopic": len(off), "gate": settings.gate_vector_threshold,
            "cutoff": settings.relative_cutoff, "bm25_protect": settings.bm25_protect,
            "summary": summary, "pool_ceiling_hit": pool_hit, "timing": timing,
        }, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
