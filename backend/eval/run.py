"""Retrieval eval: `python -m eval.run` from backend/.

Two modes, chosen by the dataset:
  * "project": a folder path   -> ingest it into a throwaway project, evaluate, delete it (sample project)
  * "existing_project": a name -> evaluate an already ingested project (no ingest, nothing deleted)

Every question goes through the real pipeline once (real Postgres + ES, real Gemini query embedding, no
reranker) with the gate and the filter switched off; the gate (top vector similarity >= t), the relative
filter and the selection stage (overlap -> budget -> Top-K) are then replayed offline at a sweep of gate
thresholds. Query embeddings are cached in eval/.cache so a re-run, or a re-run after a quota error, costs
no API calls for questions already embedded.

Question kinds
  answerable   `expected` files must be in the context (Recall@5, MRR); `acceptable` files also count as
               a hit but are not required. A hit = any expected/acceptable file in the final context.
  offtopic     the pipeline should return NoRelevantContext.
  borderline   reported, never used to choose the threshold (arguably answerable, or documented-but-unbuilt).

Gate objective: J = (1 - w) * hit_rate(answerable) + w * refuse_rate(offtopic), w = --refuse-weight.

Options: --dataset PATH  --project NAME  --cutoff C  --protect N  --keep  -v/-vv  --limit N
         --bootstrap N  --refuse-weight W  --holdout F  --seed S
"""
import argparse
import asyncio
import json
import math
import random
import sys
import time
import uuid
from collections import defaultdict
from pathlib import Path

import httpx
import numpy as np
from sqlalchemy import select

from app.config import REPO_ROOT, settings
from app.db import SessionLocal
from app.ingestion import worker
from app.main import app
from app.models import File, Project
from app.providers.embeddings import EmbeddingError, get_embedding_provider, set_embedding_provider
from app.retrieval import pipeline
from app.retrieval.selection import gate_passes, relative_filter, select as select_context

EVAL_DIR = Path(__file__).parent
DEFAULT_DATASET = EVAL_DIR / "dataset.json"
CACHE_PATH = EVAL_DIR / ".cache" / "query_embeddings.json"
SKIP_DIRS = {"node_modules", ".git", ".venv", "__pycache__"}


# ---------------------------------------------------------------- query embedding cache
class CachingEmbedder:
    """Wraps the configured provider; embed_query is cached on disk by (fingerprint, text)."""

    def __init__(self, inner):
        self.inner = inner
        self.provider, self.model, self.dims = inner.provider, inner.model, inner.dims
        try:
            self.cache: dict[str, list[float]] = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}

    @property
    def fingerprint(self) -> str:
        return self.inner.fingerprint

    def _key(self, text: str) -> str:
        return f"{self.inner.fingerprint}|{text}"

    def cached(self, text: str) -> bool:
        return self._key(text) in self.cache

    async def embed_documents(self, texts):
        return await self.inner.embed_documents(texts)

    async def embed_query(self, text):
        key = self._key(text)
        if key in self.cache:
            return self.cache[key]
        vec = await self.inner.embed_query(text)
        self.cache[key] = vec
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self.cache), encoding="utf-8")
        return vec


# ---------------------------------------------------------------- sample-project ingest (old mode)
def _sample_files(root: Path) -> list[tuple[Path, str]]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not SKIP_DIRS & set(rel.parts) and rel.name != ".env":
            out.append((p, rel.as_posix()))
    return out


async def _ingest(client: httpx.AsyncClient, root: Path) -> str:
    res = await client.post("/projects", json={"name": f"eval-{int(time.time())}"})
    res.raise_for_status()
    pid = res.json()["id"]
    items = [(rel, p.read_bytes()) for p, rel in _sample_files(root)]
    if not items:
        raise SystemExit(f"no sample files under {root}")
    up = await client.post(
        f"/projects/{pid}/upload",
        files=[("files", (rel.rsplit("/", 1)[-1], data)) for rel, data in items],
        data={"paths": [rel for rel, _ in items]},
    )
    if up.status_code >= 400:
        raise SystemExit(f"upload failed: {up.status_code} {up.text[:400]}")
    await worker.process_project(uuid.UUID(pid), SessionLocal)
    return pid


async def _find_project(ref: str) -> tuple[uuid.UUID, str]:
    async with SessionLocal() as s:
        row = None
        try:
            row = await s.get(Project, uuid.UUID(ref))
        except ValueError:
            pass
        if row is None:
            rows = (await s.scalars(select(Project).where(Project.name == ref).order_by(Project.created_at.desc()))).all()
            row = rows[0] if rows else None
        if row is None:
            raise SystemExit(f"project {ref!r} not found")
        return row.id, row.name


# ---------------------------------------------------------------- scoring
def _norm(path: str, prefix: str) -> str:
    return path[len(prefix):] if prefix and path.startswith(prefix) else path


CUTOFF, PROTECT = settings.relative_cutoff, settings.bm25_protect  # overridden by --cutoff / --protect


def _outcome(item: dict, gate: float, prefix: str, cutoff: float | None = None) -> dict:
    """What chat would receive for `item` at this gate threshold: gate -> relative filter -> overlap / budget / Top-K."""
    cutoff = CUTOFF if cutoff is None else cutoff
    served = []
    if gate_passes(item["top_vec"], gate):
        survivors = relative_filter(list(item["ranked"]), item["top_vec"], cutoff, PROTECT)
        served = select_context(survivors, -math.inf, settings.retrieval_final_top_k, settings.retrieval_context_tokens)
    files = list(dict.fromkeys(_norm(c.file_path, prefix) for c in served))
    out = {"files": files, "context": bool(files), "chunks": len(served)}
    if item["kind"] == "answerable":
        exp, ok = set(item["expected"]), set(item["expected"]) | set(item["acceptable"])
        out["hit"] = any(f in ok for f in files)
        out["recall"] = len(set(files) & exp) / len(exp)
        ranks = [i for i, f in enumerate(files, 1) if f in ok]
        out["mrr"] = 1 / ranks[0] if ranks else 0.0
        out["share"] = _mean([_norm(c.file_path, prefix) in ok for c in served]) if served else float("nan")
    return out


def _mean(xs) -> float:
    xs = [x for x in xs if x == x]  # ignore NaN
    return float(np.mean(xs)) if len(xs) else float("nan")


def _matrix(rows: list[dict], grid: np.ndarray, prefix: str, key) -> np.ndarray:
    return np.array([[key(_outcome(r, t, prefix)) for t in grid] for r in rows], dtype=float).reshape(len(rows), len(grid))


def _summary(rows: list[dict], grid: np.ndarray, prefix: str, w: float) -> dict:
    ans = [r for r in rows if r["kind"] == "answerable"]
    off = [r for r in rows if r["kind"] == "offtopic"]
    return {
        "ans": ans, "off": off, "w": w,
        "hit": _matrix(ans, grid, prefix, lambda o: o["hit"]),
        "rec": _matrix(ans, grid, prefix, lambda o: o["recall"]),
        "mrr": _matrix(ans, grid, prefix, lambda o: o["mrr"]),
        "ref": _matrix(off, grid, prefix, lambda o: not o["context"]),
    }


def _objective(hit: np.ndarray, ref: np.ndarray, w: float) -> np.ndarray:
    return (1 - w) * hit.mean(axis=0) + w * ref.mean(axis=0)


def _plateau(j: np.ndarray, grid: np.ndarray, tol: float = 1e-9) -> tuple[float, float, float]:
    """(low, high, midpoint) of the thresholds that reach the maximum objective."""
    top = np.flatnonzero(j >= j.max() - tol)
    lo, hi = float(grid[top[0]]), float(grid[top[-1]])
    return lo, hi, round((lo + hi) / 2, 2)


def _bootstrap(S: dict, grid: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    na, no = S["hit"].shape[0], S["ref"].shape[0]
    picks = []
    for _ in range(n):
        ia, io = rng.integers(0, na, na), rng.integers(0, no, no)
        picks.append(_plateau(_objective(S["hit"][ia], S["ref"][io], S["w"]), grid)[2])
    return np.array(picks)


# ---------------------------------------------------------------- main
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(DEFAULT_DATASET))
    ap.add_argument("--project", help="evaluate this existing project (name or id), overriding the dataset")
    ap.add_argument("--cutoff", type=float, help=f"relative cut-off to replay (default {settings.relative_cutoff})")
    ap.add_argument("--protect", type=int, help=f"BM25 hits exempt from the filter (default {settings.bm25_protect})")
    ap.add_argument("--keep", action="store_true", help="sample mode: keep the throwaway project")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions (smoke test)")
    ap.add_argument("--bootstrap", type=int, default=500)
    ap.add_argument("--refuse-weight", type=float, default=0.5, help="weight of refusing off-topic vs hitting answerable")
    ap.add_argument("--holdout", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    global CUTOFF, PROTECT
    CUTOFF = settings.relative_cutoff if args.cutoff is None else args.cutoff
    PROTECT = settings.bm25_protect if args.protect is None else args.protect

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    questions = data["questions"]
    if args.limit:
        questions = questions[: args.limit]
    prefix = data.get("path_prefix", "")
    exclude = set(data.get("exclude", []))
    for q in questions:  # old-format datasets have no kind / acceptable
        q.setdefault("acceptable", [])
        q.setdefault("kind", "answerable" if q["expected"] else "offtopic")
        q.setdefault("tag", "all")

    embedder = CachingEmbedder(get_embedding_provider())
    set_embedding_provider(embedder)
    worker.enqueue = lambda _pid: None  # never start the background consumer from the eval

    existing = args.project or data.get("existing_project")
    client = None
    sample_pid: str | None = None
    if existing:
        pid, name = await _find_project(existing)
        label = f"existing project {name!r}"
    else:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=300)
        sample_pid = await _ingest(client, REPO_ROOT / data["project"])
        pid = uuid.UUID(sample_pid)
        label = "throwaway sample project"

    try:
        async with SessionLocal() as session:
            ready = sorted(_norm(p, prefix) for p in (await session.scalars(
                select(File.file_path).where(File.project_id == pid, File.status == "ready")
            )).all())
            print(f"{label} {pid}: {len(ready)} ready file(s); replaying cut-off {CUTOFF}, BM25 protect {PROTECT}, no reranker")
            missing = {e for q in questions for e in q["expected"] + q["acceptable"]} - set(ready)
            if missing:
                print(f"WARNING: expected files not ready: {sorted(missing)}")

            # 1. embed every query up front (cached), so a quota error stops cleanly and resumes later
            todo = [q["q"] for q in questions if not embedder.cached(q["q"])]
            print(f"{len(questions)} questions; {len(questions) - len(todo)} query embeddings cached, {len(todo)} to fetch")
            for n, text in enumerate(todo, 1):
                try:
                    await embedder.embed_query(text)
                except EmbeddingError as exc:
                    print(f"\nSTOPPED after {n - 1}/{len(todo)} new query embeddings: {str(exc)[:160]}")
                    print("Cached embeddings are kept; re-run later to resume without repeating them.")
                    return 2

            # 2. run the pipeline once per question with the gate and filter off; they are replayed below
            rows = []
            for i, q in enumerate(questions, 1):
                trace = await pipeline.run(
                    session, pid, q["q"], gate_threshold=-math.inf, relative_cutoff=0, rerank_top_n=0,
                )
                ranked = [c for c in trace.candidates if _norm(c.file_path, prefix) not in exclude]
                scores = [c.vector_score for c in ranked if c.vector_score is not None]
                rows.append({**q, "ranked": ranked, "top_vec": max(scores) if scores else None})
                if i % 50 == 0:
                    print(f"  ran {i}/{len(questions)}")
    finally:
        if client is not None:
            if sample_pid and not args.keep:
                await client.delete(f"/projects/{sample_pid}")
            await client.aclose()

    scored = [r for r in rows if r["kind"] in ("answerable", "offtopic")]
    if not any(r["kind"] == "answerable" for r in scored) or not any(r["kind"] == "offtopic" for r in scored):
        print("need both answerable and offtopic questions to choose a gate threshold")
        return 1

    tops = [r["top_vec"] for r in scored if r["top_vec"] is not None]
    grid = np.round(np.arange(math.floor(min(tops) * 200) / 200 - 0.005, max(tops) + 0.01, 0.0025), 4)

    rng = random.Random(args.seed)
    ordered = sorted(scored, key=lambda r: r["q"])
    rng.shuffle(ordered)
    n_hold = int(len(ordered) * args.holdout)
    holdout, tune = ordered[:n_hold], ordered[n_hold:]

    w = args.refuse_weight
    full, tuned, held = (_summary(x, grid, prefix, w) for x in (scored, tune, holdout))
    j_full = _objective(full["hit"], full["ref"], w)
    j_tune = _objective(tuned["hit"], tuned["ref"], w)
    j_hold = _objective(held["hit"], held["ref"], w)
    k_cfg = int(np.argmin(np.abs(grid - settings.gate_vector_threshold)))

    n_ans, n_off = len(full["ans"]), len(full["off"])
    print(f"\nscored: {n_ans} answerable + {n_off} off-topic ({len(rows) - len(scored)} borderline reported separately)")
    print(f"\n{'gate':>9} {'hit':>6} {'Recall@5':>9} {'MRR':>6} {'refused':>8} {'J':>6}")
    i_best = int(np.argmax(j_full))
    shown = {int(i) for i in range(0, len(grid), 20)} | set(range(max(0, i_best - 20), min(len(grid), i_best + 21), 5))
    shown.add(k_cfg)
    for k in sorted(shown):
        tag = "  <- configured" if k == k_cfg else ""
        print(f"{grid[k]:>9.4f} {full['hit'][:, k].mean():>6.3f} {full['rec'][:, k].mean():>9.3f} "
              f"{full['mrr'][:, k].mean():>6.3f} {full['ref'][:, k].mean():>8.3f} {j_full[k]:>6.3f}{tag}")

    # ---- choosing the gate threshold
    plo, phi, pmid = _plateau(j_full, grid)
    near = np.flatnonzero(j_full >= j_full.max() - 0.01)
    t_tune = _plateau(j_tune, grid)[2]
    k_tune = int(np.argmin(np.abs(grid - t_tune)))
    picks = _bootstrap(full, grid, args.bootstrap, np.random.default_rng(args.seed))
    p5, p50, p95 = (float(np.percentile(picks, p)) for p in (5, 50, 95))
    k_mid = int(np.argmin(np.abs(grid - pmid)))

    print("\n=== gate threshold selection ===")
    print(f"best objective J = {j_full.max():.3f} on gates {plo:.4f} .. {phi:.4f} (midpoint {pmid:.4f})")
    print(f"within 0.01 of best: {grid[near[0]]:.4f} .. {grid[near[-1]]:.4f}  (width {grid[near[-1]] - grid[near[0]]:.4f})")
    print(f"tune split ({len(tune)} q) picks {t_tune:.4f}; held-out {len(holdout)} q: J={j_hold[k_tune]:.3f} "
          f"(best possible on held-out {j_hold.max():.3f})")
    print(f"bootstrap x{args.bootstrap}: median {p50:.4f}, 5-95% range {p5:.4f} .. {p95:.4f}")
    print(f"configured {settings.gate_vector_threshold}: J={j_full[k_cfg]:.3f}  | midpoint {pmid:.4f}: J={j_full[k_mid]:.3f}")
    print(f"\nSUGGESTED GATE THRESHOLD: {p50:.4f}  (bootstrap median; best-plateau midpoint is {pmid:.4f})")

    t_use = float(grid[int(np.argmin(np.abs(grid - settings.gate_vector_threshold)))])
    print(f"\nEverything below is at the CONFIGURED gate {t_use:.4f}, cut-off {CUTOFF}, BM25 protect {PROTECT}.")

    # ---- the relative cut-off: what the filter buys and costs at this gate
    print(f"\n=== relative cut-off at gate {t_use:.4f} (share = served chunks from an expected/acceptable file) ===")
    print(f"{'cut-off':>8} {'hit':>6} {'Recall@5':>9} {'MRR':>6} {'share':>6} {'chunks':>6}")
    for c in (0, 0.02, 0.03, 0.04, 0.05, 0.06):
        outs = [_outcome(r, t_use, prefix, cutoff=c) for r in full["ans"]]
        tag = "  <- configured" if abs(c - CUTOFF) < 1e-9 else ("  (filter off)" if c == 0 else "")
        print(f"{c:>8.3f} {_mean([o['hit'] for o in outs]):>6.3f} {_mean([o['recall'] for o in outs]):>9.3f} "
              f"{_mean([o['mrr'] for o in outs]):>6.3f} {_mean([o['share'] for o in outs if o['context']]):>6.3f} "
              f"{_mean([o['chunks'] for o in outs if o['context']]):>6.2f}{tag}")

    # ---- per-tag breakdown
    print("\n=== per tag ===")
    by_tag = defaultdict(list)
    for r in scored:
        by_tag[(r["kind"], r["tag"])].append(_outcome(r, t_use, prefix))
    for (kind, tag), outs in sorted(by_tag.items()):
        if kind == "answerable":
            print(f"  {tag:<12} n={len(outs):<3} hit={_mean([o['hit'] for o in outs]):.2f} "
                  f"recall@5={_mean([o['recall'] for o in outs]):.2f} mrr={_mean([o['mrr'] for o in outs]):.2f}")
        else:
            print(f"  {tag:<12} n={len(outs):<3} refused={_mean([not o['context'] for o in outs]):.2f}   (off-topic)")

    # ---- borderline
    def top_of(r) -> str:
        c = r["ranked"][0] if r["ranked"] else None
        return "-" if c is None else f"{_norm(c.file_path, prefix)} vec={r['top_vec']:.3f}"

    border = [r for r in rows if r["kind"] == "borderline"]
    if border:
        print("\n=== borderline (not used for tuning) ===")
        for r in border:
            o = _outcome(r, t_use, prefix)
            print(f"  {'context' if o['context'] else 'refused':<8} {r['q']}  [top: {top_of(r)}]")

    # ---- failures
    print("\n=== failures ===")
    for r in scored:
        o = _outcome(r, t_use, prefix)
        top = ", ".join(f"{_norm(c.file_path, prefix)}:{c.line_start}={c.vector_score or 0:.3f}" for c in r["ranked"][:3])
        if r["kind"] == "answerable" and not o["hit"]:
            why = "GATE" if not o["context"] else "FILTER/ORDER"
            print(f"  MISS ({why}) [{r['tag']}] {r['q']}\n        expected {r['expected']}\n        got      {top}")
        elif r["kind"] == "offtopic" and o["context"]:
            print(f"  LEAK  [{r['tag']}] {r['q']}\n        got      {top}")

    if args.verbose:
        print("\n=== per question (vector similarity, RRF order) ===")
        for r in rows:
            top = ", ".join(f"{_norm(c.file_path, prefix)}:{c.line_start}={c.vector_score or 0:.3f}"
                            for c in r["ranked"][: (99 if args.verbose > 1 else 4)])
            print(f"  [{r['kind'][:3].upper()}] {r['q']}\n        {top}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
