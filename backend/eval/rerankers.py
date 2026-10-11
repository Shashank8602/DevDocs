"""Reranker bake-off: `python -m eval.rerankers` from backend/.

Scores the RRF top N of every recorded question (eval/.cache/pipeline_records.json, written by
`eval.experiments`) with each candidate cross-encoder, and caches the scores per model in
eval/.cache/rerank_<model>.json. `eval.strategies` then judges every model on the same scorecard.

No API calls: chunk text is read from Postgres, the models run locally (fastembed ONNX). Each model is
first timed on a few questions; a model too slow for the latency budget even on the top 5 is skipped.
Runs are resumable: questions already in a model's cache are not scored again."""
import argparse
import asyncio
import json
import statistics
import sys
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, File
from app.retrieval.reranker import CrossEncoderReranker
from eval.ablation import TimedReranker
from eval.experiments import RECORDS, by_rrf, to_candidates

MODELS = [
    "Xenova/ms-marco-MiniLM-L-6-v2",  # the shipped model, re-timed under the same conditions
    "Xenova/ms-marco-MiniLM-L-12-v2",
    "jinaai/jina-reranker-v1-turbo-en",
    "jinaai/jina-reranker-v2-base-multilingual",
    "BAAI/bge-reranker-base",
    "BAAI/bge-reranker-v2-m3-int8",
]
BUDGET_MS = 1900  # retrieval budget is about 2 s; ES + Postgres + RRF take about 80 ms
PROBE = 12  # questions used to time a model before the full run


def cache_path(model: str) -> Path:
    return RECORDS.with_name(f"rerank_{model.replace('/', '__')}.json")


async def load_passages(chunk_ids: set[str]) -> dict[str, str]:
    """chunk_id -> "{file_path}\\n{content}", exactly the text the pipeline sends to the reranker."""
    out = {}
    ids = [uuid.UUID(c) for c in chunk_ids]
    async with SessionLocal() as session:
        for i in range(0, len(ids), 500):
            rows = await session.execute(
                select(Chunk.id, File.file_path, Chunk.content).join(File, File.id == Chunk.file_id)
                .where(Chunk.id.in_(ids[i:i + 500]))
            )
            out.update({str(cid): f"{path}\n{content}" for cid, path, content in rows})
    return out


async def bake(model: str, work: list[tuple[str, list[str]]], passages: dict[str, str]) -> dict:
    path = cache_path(model)
    blob = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"model": model, "scores": {}}
    timed = TimedReranker(CrossEncoderReranker(model))
    todo = [(q, ids) for q, ids in work if q not in blob["scores"]]
    print(f"\n{model}: {len(work) - len(todo)} cached, {len(todo)} to score", flush=True)
    for i, (q, ids) in enumerate(todo, 1):
        scores = await timed.score(q, [passages[c] for c in ids])
        blob["scores"][q] = dict(zip(ids, scores))
        if i == PROBE + 1 and "ms_per_passage" not in blob:  # first call loads the model: skip it
            d, n = timed.durations[1:], timed.sizes[1:]
            mpp = 1000 * sum(d) / sum(n)
            print(f"  probe: {mpp:.0f} ms per passage -> top 10 ~{10 * mpp:.0f} ms, top 5 ~{5 * mpp:.0f} ms", flush=True)
            if 5 * mpp > BUDGET_MS:
                blob.update(ms_per_passage=mpp, skipped=f"too slow: {5 * mpp:.0f} ms for 5 passages")
                path.write_text(json.dumps(blob), encoding="utf-8")
                print(f"  skipped ({blob['skipped']})", flush=True)
                return blob
        if i % 50 == 0:
            path.write_text(json.dumps(blob), encoding="utf-8")
            print(f"  {i}/{len(todo)}", flush=True)
    if len(timed.durations) > 1:
        d, n = timed.durations[1:], timed.sizes[1:]
        blob["ms_per_passage"] = 1000 * sum(d) / sum(n)
        blob["ms_per_question_p50"] = 1000 * statistics.median(d)
    path.write_text(json.dumps(blob), encoding="utf-8")
    print(f"  done: {blob.get('ms_per_passage', float('nan')):.0f} ms per passage", flush=True)
    return blob


async def main_async(args) -> None:
    recs = json.loads(RECORDS.read_text(encoding="utf-8"))["records"]
    work = [(r["q"], [c.chunk_id for c in by_rrf(to_candidates(r))[: args.top_n]]) for r in recs]
    passages = await load_passages({c for _, ids in work for c in ids})
    missing = {c for _, ids in work for c in ids} - passages.keys()
    if missing:
        raise SystemExit(f"{len(missing)} recorded chunks are no longer in Postgres; re-record with eval.experiments")
    print(f"{len(work)} questions, top {args.top_n} each, {len(passages)} distinct passages", flush=True)
    for m in args.models or MODELS:
        await bake(m, work, passages)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", help="default: all candidates")
    ap.add_argument("--top-n", type=int, default=10)
    asyncio.run(main_async(ap.parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
