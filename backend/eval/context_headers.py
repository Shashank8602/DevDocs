"""Does embedding "path > section\n content" beat embedding the bare content?  Scratch-index experiment.

  python -m eval.context_headers embed   # resumable: embeds every chunk of the project with a header (~1 call/chunk)
  python -m eval.context_headers eval    # builds a scratch ES index from the cached vectors and compares

The real index and project are never modified. Query embeddings come from the existing cache."""
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, File
from app.providers.embeddings import EmbeddingError, get_embedding_provider
from app.search import es_client
from app.search.index_manager import ensure_index, index_name

CACHE = Path(__file__).parent / ".cache" / "ctx_vectors.json"
FP = "scratch:ctx-headers:1536"


def header(path: str, section: str | None, content: str) -> str:
    return f"{path}{' > ' + section if section else ''}\n{content}"


async def load_chunks(project_name: str):
    from eval.run import _find_project
    pid, _ = await _find_project(project_name)
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Chunk.id, Chunk.content, Chunk.section, File.file_path, File.id, File.file_type)
            .join(File, File.id == Chunk.file_id).where(Chunk.project_id == pid, File.status == "ready")
            .order_by(File.file_path, Chunk.ordinal)
        )).all()
    return pid, rows


async def embed(project="DevDocs"):
    pid, rows = await load_chunks(project)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = [r for r in rows if str(r[0]) not in cache]
    print(f"{len(rows)} chunks, {len(todo)} to embed")
    provider = get_embedding_provider()
    for i in range(0, len(todo), 20):
        batch = todo[i:i + 20]
        try:
            vecs = await provider.embed_documents([header(r[3], r[2], r[1]) for r in batch])
        except EmbeddingError as exc:
            print("STOPPED:", str(exc)[:200])
            break
        for r, v in zip(batch, vecs):
            cache[str(r[0])] = v
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache))
        print(f"  {len(cache)}/{len(rows)}", flush=True)


async def build_index(project="DevDocs"):
    from elasticsearch.helpers import async_bulk
    pid, rows = await load_chunks(project)
    cache = json.loads(CACHE.read_text())
    name = await ensure_index(FP, 1536)
    if await es_client.es.count(index=name) and (await es_client.es.count(index=name))["count"] >= len(rows):
        return pid
    await es_client.es.delete_by_query(index=name, query={"match_all": {}}, refresh=True)
    acts = ({"_index": name, "_id": str(cid), "_source": {
        "project_id": str(pid), "file_id": str(fid), "file_path": path, "file_type": ft, "content": content,
        "embedding": cache[str(cid)], "path_text": path, "section": section}}
        for cid, content, section, path, fid, ft in rows if str(cid) in cache)
    await async_bulk(es_client.es, acts, refresh="wait_for")
    return pid


async def evaluate():
    import numpy as np
    from app.providers.embeddings import set_embedding_provider
    from app.retrieval import pipeline
    from eval.run import CachingEmbedder, _norm

    embedder = CachingEmbedder(get_embedding_provider())

    async def strict(text):
        if not embedder.cached(text):
            raise RuntimeError("uncached query")
        return await CachingEmbedder.embed_query(embedder, text)
    embedder.embed_query = strict
    set_embedding_provider(embedder)
    pid = await build_index()
    real_resolve = pipeline.resolve_fingerprint

    async def scratch(session, project_id):
        return FP
    gates = [0.78, 0.80, 0.81, 0.82, 0.83, 0.84, 0.85, 0.86, 0.88]
    for ds in ("dataset_devdocs.json", "dataset_devdocs_holdout.json"):
        data = json.loads((Path(__file__).parent / ds).read_text(encoding="utf-8"))
        prefix = data.get("path_prefix", "")
        qs = [q for q in data["questions"] if embedder.cached(q["q"])]
        print(f"\n== {ds} ({len(qs)} questions)")
        for label, resolver in (("bare content (production)", real_resolve), ("path > section header", scratch)):
            pipeline.resolve_fingerprint = resolver
            print(f"-- {label}")
            print(f"{'gate':>6} {'hit':>6} {'R@5':>6} {'MRR':>6} {'share':>6} {'refused':>8}")
            async with SessionLocal() as s:
                # one run with the gate off records top similarity; thresholds are replayed from the traces
                traces = []
                for q in qs:
                    t = await pipeline.run(s, pid, q["q"], gate_threshold=-1.0)
                    traces.append((q, t))
            for g in gates:
                ans, off = [], []
                for q, t in traces:
                    passed = t.top_vector_score is not None and t.top_vector_score >= g
                    final = t.final if passed else []
                    files = list(dict.fromkeys(_norm(c.file_path, prefix) for c in final))
                    if q["kind"] == "answerable":
                        ok = set(q["expected"]) | set(q["acceptable"])
                        ranks = [i for i, f in enumerate(files, 1) if f in ok]
                        ans.append((bool(ranks), len(set(files) & set(q["expected"])) / len(q["expected"]),
                                    1 / ranks[0] if ranks else 0,
                                    np.mean([_norm(c.file_path, prefix) in ok for c in final]) if final else np.nan))
                    elif q["kind"] == "offtopic":
                        off.append(not final)
                a = np.array(ans, dtype=float)
                print(f"{g:6.2f} {a[:,0].mean():6.3f} {a[:,1].mean():6.3f} {a[:,2].mean():6.3f} "
                      f"{np.nanmean(a[:,3]):6.3f} {np.mean(off):8.3f}")
    pipeline.resolve_fingerprint = real_resolve
    await es_client.es.close()


if __name__ == "__main__":
    asyncio.run({"embed": embed, "eval": evaluate}[sys.argv[1] if len(sys.argv) > 1 else "embed"]())
