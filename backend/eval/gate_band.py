"""Inside the narrow band of top vector similarity around the gate, does BM25 evidence separate answerable
questions from off-topic ones?  `python -m eval.gate_band` (cached query embeddings only)."""
import asyncio
import json
from pathlib import Path

import numpy as np

from app.db import SessionLocal
from app.providers.embeddings import get_embedding_provider, set_embedding_provider
from app.retrieval import pipeline
from app.retrieval.query import keyword_query
from app.search import es_client
from eval.run import CachingEmbedder, _find_project

EVAL = Path(__file__).parent


async def main():
    emb = CachingEmbedder(get_embedding_provider())
    set_embedding_provider(emb)
    rows = []
    for ds in ("dataset_devdocs.json", "dataset_devdocs_holdout.json"):
        data = json.loads((EVAL / ds).read_text(encoding="utf-8"))
        pid, _ = await _find_project(data["existing_project"])
        async with SessionLocal() as s:
            for q in data["questions"]:
                if not emb.cached(q["q"]) or q["kind"] == "borderline":
                    continue
                t = await pipeline.run(s, pid, q["q"], gate_threshold=-1.0)
                terms = keyword_query(q["q"]).split()
                top = t.bm25[0].score if t.bm25 else 0.0
                # how many of the question's content terms appear (case-insensitively) in the best BM25 chunk
                body = (t.bm25[0].content + " " + t.bm25[0].file_path).lower() if t.bm25 else ""
                cover = np.mean([w.lower() in body for w in terms]) if terms else 0.0
                rows.append((ds[8:14], q["kind"], t.top_vector_score or 0.0, top, cover, len(terms)))
    await es_client.es.close()
    a = np.array([(r[2], r[3], r[4], r[5]) for r in rows], dtype=float)
    kind = np.array([r[1] for r in rows])
    for lo, hi in ((0.0, 0.78), (0.78, 0.82), (0.82, 0.86), (0.86, 1.0)):
        m = (a[:, 0] >= lo) & (a[:, 0] < hi)
        for k in ("answerable", "offtopic"):
            mm = m & (kind == k)
            if mm.sum():
                print(f"vec [{lo:.2f},{hi:.2f}) {k:10} n={mm.sum():3}  bm25 top median {np.median(a[mm,1]):5.1f}  "
                      f"p25 {np.percentile(a[mm,1],25):5.1f} p75 {np.percentile(a[mm,1],75):5.1f}  term cover median {np.median(a[mm,2]):.2f}")
    # candidate rule inside the band [0.80, 0.82): pass when BM25 cover >= c and bm25 top >= b
    band = (a[:, 0] >= 0.80) & (a[:, 0] < 0.82)
    print("\nband [0.80,0.82): rescued answerable vs leaked off-topic for rule cover>=c & top>=b")
    for c in (0.5, 0.67, 0.8, 1.0):
        for b in (8, 12, 16):
            rule = band & (a[:, 2] >= c) & (a[:, 1] >= b)
            print(f"  c>={c:.2f} b>={b:2}: +{int((rule & (kind=='answerable')).sum()):2} answerable, +{int((rule & (kind=='offtopic')).sum()):2} off-topic leaks"
                  f"   (band has {int((band & (kind=='answerable')).sum())} answerable / {int((band & (kind=='offtopic')).sum())} off-topic)")


asyncio.run(main())
