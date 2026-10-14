"""Offline comparison of BM25-leg query variants through the real pipeline (cached query embeddings only,
so no API calls).  `python -m eval.bm25_variants` from backend/."""
import asyncio
import json
import re
import sys
from pathlib import Path

import numpy as np

from app.config import settings
from app.db import SessionLocal
from app.providers.embeddings import get_embedding_provider, set_embedding_provider
from app.retrieval import bm25 as bm25_mod
from app.retrieval import pipeline
from app.retrieval.bm25 import Hit, scope_filter, to_hits
from app.search import es_client
from app.search.index_manager import index_name
from eval.run import CachingEmbedder, _find_project, _norm

EVAL = Path(__file__).parent
STOP = set("""a an the is are was were be been being do does did doing how what where when why which who whom
this that these those it its i me my we our you your they them their of in on at to for from by with about into
over under can could should would will shall may might must and or but if then than so as not no yes there here
please tell show explain give find work works working used use using get gets make makes made""".split())


def content_terms(q: str) -> str:
    words = re.findall(r"[A-Za-z0-9_.\-/]+", q)
    kept = [w for w in words if w.lower() not in STOP]
    return " ".join(kept) or q


def _search(fp, pid, ids, query_body, k):
    async def go():
        resp = await es_client.es.search(
            index=index_name(fp), size=k, query=query_body,
            source_includes=["file_id", "file_path", "content"],
        )
        return to_hits(resp)
    return go()


def v_base(fp, pid, ids, q, k):
    return _search(fp, pid, ids, {"bool": {"must": [{"match": {"content": q}}], "filter": scope_filter(pid, ids)}}, k)


def make_multi(fields, q_fn=lambda q: q, tie=0.3, kind="best_fields"):
    def v(fp, pid, ids, q, k):
        body = {"bool": {"must": [{"multi_match": {
            "query": q_fn(q), "fields": fields, "type": kind, **({"tie_breaker": tie} if kind == "best_fields" else {}),
        }}], "filter": scope_filter(pid, ids)}}
        return _search(fp, pid, ids, body, k)
    return v


def make_should(path_boost, sec_boost, q_fn=lambda q: q):
    def v(fp, pid, ids, q, k):
        qq = q_fn(q)
        body = {"bool": {
            "should": [
                {"match": {"content": qq}},
                {"match": {"path_text": {"query": qq, "boost": path_boost}}},
                {"match": {"section": {"query": qq, "boost": sec_boost}}},
            ], "minimum_should_match": 1, "filter": scope_filter(pid, ids)}}
        return _search(fp, pid, ids, body, k)
    return v


from collections import defaultdict
from app.retrieval import fusion as fusion_mod
from app.retrieval.fusion import rrf_fuse as _orig_fuse

BEST_BM25 = make_should(1.0, 1.0, content_terms)


def make_fuse(file_alpha=0.0, w_bm=1.0, w_vec=1.0):
    def fuse(bm25, vector, k=60):
        fused = _orig_fuse([], [], k)
        by = {}
        for w, hits, leg in ((w_bm, bm25, "bm25"), (w_vec, vector, "vector")):
            for rank, h in enumerate(hits, 1):
                f = by.get(h.chunk_id)
                if f is None:
                    f = by[h.chunk_id] = fusion_mod.FusedHit(h.chunk_id, h.file_id, h.file_path, h.content, 0.0, None, None)
                f.rrf_score += w / (k + rank)
                if leg == "bm25":
                    f.bm25_rank = rank
                else:
                    f.vector_rank, f.vector_score = rank, h.score
        if file_alpha:
            tot = defaultdict(float)
            for f in by.values():
                tot[f.file_id] += f.rrf_score
            for f in by.values():
                f.rrf_score += file_alpha * (tot[f.file_id] - f.rrf_score)
        return sorted(by.values(), key=lambda f: f.rrf_score, reverse=True)
    return fuse


TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|specs?|fixtures)/|(^|/)test_[^/]+\.py$|\.(test|spec)\.[jt]sx?$", re.I)
MIGRATION = re.compile(r"(^|/)(alembic|migrations)/", re.I)


def make_prior(test_factor=1.0, migration_factor=1.0):
    base = make_fuse()
    state = {"q": ""}

    def fuse(bm25, vector, k=60):
        out = base(bm25, vector, k)
        q = state["q"].lower()
        for f in out:
            if TEST_PATH.search(f.file_path) and "test" not in q:
                f.rrf_score *= test_factor
            if MIGRATION.search(f.file_path) and "migrat" not in q and "alembic" not in q and "schema" not in q:
                f.rrf_score *= migration_factor
        return sorted(out, key=lambda f: f.rrf_score, reverse=True)
    fuse.state = state
    return fuse


VARIANTS = {
    "B: new bm25": (BEST_BM25, make_fuse(), 10),
    "B + tests x0.9": (BEST_BM25, make_prior(0.9), 10),
    "B + tests x0.8": (BEST_BM25, make_prior(0.8), 10),
    "B + tests x0.7": (BEST_BM25, make_prior(0.7), 10),
    "B + tests x0.6": (BEST_BM25, make_prior(0.6), 10),
    "B + tests x0.8 + migr x0.8": (BEST_BM25, make_prior(0.8, 0.8), 10),
    "B + tests x0.7 + migr x0.7": (BEST_BM25, make_prior(0.7, 0.7), 10),
}


async def evaluate(name, spec, questions, pid, prefix, exclude):
    fn, fuse, leg_k = spec
    pipeline.bm25_search = fn
    pipeline.rrf_fuse = fuse
    settings.retrieval_leg_k = leg_k
    ans, off = [], []
    async with SessionLocal() as s:
        for q in questions:
            if hasattr(fuse, "state"):
                fuse.state["q"] = q["q"]
            res = await pipeline.run(s, pid, q["q"])
            files = list(dict.fromkeys(_norm(c.file_path, prefix) for c in res.final))
            if q["kind"] == "answerable":
                exp, ok = set(q["expected"]), set(q["expected"]) | set(q["acceptable"])
                ranks = [i for i, f in enumerate(files, 1) if f in ok]
                ans.append({
                    "hit": bool(ranks), "rec": len(set(files) & exp) / len(exp), "mrr": 1 / ranks[0] if ranks else 0,
                    "share": np.mean([_norm(c.file_path, prefix) in ok for c in res.final]) if res.final else float("nan"),
                    # was an expected file even in the BM25 leg's top 10? (candidate pool quality)
                    "pool": any(_norm(h.file_path, prefix) in ok for h in res.bm25),
                })
            elif q["kind"] == "offtopic":
                off.append(not res.final)
    f = lambda k: float(np.nanmean([a[k] for a in ans]))
    return dict(name=name, hit=f("hit"), rec=f("rec"), mrr=f("mrr"), share=f("share"), pool=f("pool"),
                refused=float(np.mean(off)), n=len(ans), n_off=len(off))


async def main():
    embedder = CachingEmbedder(get_embedding_provider())

    async def strict(text):  # never call the API from this script
        if not embedder.cached(text):
            raise RuntimeError("uncached")
        return await CachingEmbedder.embed_query(embedder, text)
    embedder.embed_query = strict
    set_embedding_provider(embedder)
    for ds in ("dataset_devdocs.json", "dataset_devdocs_holdout.json"):
        data = json.loads((EVAL / ds).read_text(encoding="utf-8"))
        pid, _ = await _find_project(data["existing_project"])
        prefix, exclude = data.get("path_prefix", ""), set(data.get("exclude", []))
        qs = [q for q in data["questions"] if embedder.cached(q["q"])]
        print(f"\n== {ds}: {len(qs)}/{len(data['questions'])} questions cached")
        print(f"{'variant':34} {'hit':>6} {'R@5':>6} {'MRR':>6} {'share':>6} {'bm25pool':>8} {'refused':>8}")
        for name, fn in VARIANTS.items():
            r = await evaluate(name, fn, qs, pid, prefix, exclude)
            print(f"{r['name']:34} {r['hit']:6.3f} {r['rec']:6.3f} {r['mrr']:6.3f} {r['share']:6.3f} {r['pool']:8.3f} {r['refused']:8.3f}")
    await es_client.es.close()


if __name__ == "__main__":
    asyncio.run(main())
