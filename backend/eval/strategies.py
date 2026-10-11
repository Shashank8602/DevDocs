"""Full-pipeline strategy replay: `python -m eval.strategies` from backend/.

Every combination of reranker model x candidates reranked (top N) x order x precision filter x refusal gate
is judged on one scorecard: hit, Recall@5, MRR, off-topic refused, relevant share of served chunks
("precision"), chunks served and estimated time. Thresholds are chosen by 5-fold cross-validation and every
number reported is out-of-fold, so a strategy is never scored on the questions its threshold was tuned on.

Inputs (no API calls, no reranking here):
  eval/.cache/pipeline_records.json   every candidate with BM25 / vector / RRF / MiniLM-L-6 scores (eval.experiments)
  eval/.cache/rerank_<model>.json     other models' scores for the RRF top 10 (eval.rerankers), if present

Gates (question level; a refused question gets no context):
  vec    top vector similarity >= a
  rr     top rerank score of the reranked candidates >= b
  and    both;  or  either;  zsum  z(vec) + w * z(rr) >= t, z-scored on the training folds
Orders: rrf | rerank (reranked head by score, rest in RRF order) | rrf3 (rerank rank as a third RRF leg, weight w)
Filters (applied after the gate, before overlap / budget / Top-K; the best chunk always survives):
  none | topk K | rel_rr d (rerank score within d model-sigmas of the best) |
  rel_vec d (vector similarity within d of the best; BM25-only chunks dropped) | rel_vec_bm d (... but BM25 top 2 kept)
"""
import argparse
import json
import random
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

from app.retrieval.selection import select as select_context
from eval.experiments import BUDGET, INF, RECORDS, TOP_K, by_rerank, by_rrf, mean, to_candidates
from eval.rerankers import MODELS, cache_path

BASE_MS = 80  # ES + Postgres + RRF, measured in the ablation
LATENCY_BUDGET_MS = 2000
GOALS = {"hit": 0.93, "refused": 0.891, "prec": 0.53}
FOLDS = 5
RECORDED = "MiniLM-L-6 (recorded)"


def short(model: str) -> str:
    return model.split("/")[-1]


# ------------------------------------------------------------------ loading
def load():
    blob = json.loads(RECORDS.read_text(encoding="utf-8"))
    recs = [r for r in blob["records"] if r["kind"] in ("answerable", "offtopic")]
    for r in recs:
        r["_c"] = to_candidates(r)
        r["_rrf"] = by_rrf(r["_c"])
        r["_vec"] = {c["chunk_id"]: c["vector_score"] for c in r["cands"]}
    models = {RECORDED: {"scores": {r["q"]: {c["chunk_id"]: c["rerank"] for c in r["cands"]} for r in recs},
                         "ms_per_passage": blob["ms_per_passage"]}}
    for m in MODELS:
        p = cache_path(m)
        if not p.exists():
            continue
        b = json.loads(p.read_text(encoding="utf-8"))
        if b.get("skipped"):
            print(f"  {short(m)}: skipped in the bake-off ({b['skipped']})")
        elif all(r["q"] in b["scores"] for r in recs):
            models[short(m)] = b
        else:
            print(f"  {short(m)}: bake-off incomplete, not used yet")
    for b in models.values():
        vals = [s for d in b["scores"].values() for s in d.values()]
        b["sigma"] = float(np.std(vals))
    return blob, recs, models


# ------------------------------------------------------------------ served context per question
def scored_list(r, scores: dict, n: int):
    """RRF order; the first n carry the model's score, the rest are unscored (-inf)."""
    head = {c.chunk_id for c in r["_rrf"][:n]}
    return [replace(c, rerank_score=scores[c.chunk_id] if c.chunk_id in head else -INF) for c in r["_rrf"]]


def order(kind: str, w: float, lst):
    if kind == "rrf":
        return lst
    scored = [c for c in lst if c.rerank_score > -INF]
    if kind == "rerank":
        return by_rerank(scored) + [c for c in lst if c.rerank_score == -INF]
    rank = {c.chunk_id: i for i, c in enumerate(by_rerank(scored), 1)}  # rrf3
    return sorted(lst, key=lambda c: -(c.rrf_score + (w / (60 + rank[c.chunk_id]) if c.chunk_id in rank else 0.0)))


def apply_filter(kind: str, p: float, lst, r, sigma: float):
    if kind == "none":
        return lst, TOP_K
    if kind == "topk":
        return lst, int(p)
    if kind == "rel_rr":
        best = max(c.rerank_score for c in lst)
        return [c for c in lst if c.rerank_score >= best - p * sigma], TOP_K
    vec = r["_vec"]
    best = max(v for v in vec.values() if v is not None)
    keep_bm = kind == "rel_vec_bm"
    return [c for c in lst if (vec.get(c.chunk_id) is not None and vec[c.chunk_id] >= best - p)
            or (keep_bm and c.bm25_rank is not None and c.bm25_rank <= 2)], TOP_K


def outcome(r, kept) -> dict:
    files = list(dict.fromkeys(c.file_path for c in kept))
    if r["kind"] == "offtopic":
        return {}
    ok, exp = set(r["expected"]) | set(r["acceptable"]), set(r["expected"])
    ranks = [i for i, f in enumerate(files, 1) if f in ok]
    return {"hit": bool(ranks), "recall": len(set(files) & exp) / len(exp), "mrr": 1 / ranks[0] if ranks else 0.0,
            "prec": mean(c.file_path in ok for c in kept) if kept else float("nan"), "n": len(kept)}


# ------------------------------------------------------------------ gates
class Gates:
    """Pass matrices (params x questions) for every gate, built once per (model, top N)."""

    def __init__(self, vec: np.ndarray, rr: np.ndarray | None, folds: np.ndarray):
        self.vec, self.rr, self.folds = vec, rr, folds

    @staticmethod
    def grid(x: np.ndarray, n: int) -> np.ndarray:
        return np.unique(np.quantile(x[np.isfinite(x)], np.linspace(0, 1, n)))

    def matrices(self, train: np.ndarray) -> dict[str, tuple[np.ndarray, list]]:
        out = {}
        gv = self.grid(self.vec, 80)
        out["vec"] = (self.vec[None, :] >= gv[:, None], [{"vec": float(a)} for a in gv])
        if self.rr is None:
            return out
        gr = self.grid(self.rr, 80)
        out["rr"] = (self.rr[None, :] >= gr[:, None], [{"rr": float(b)} for b in gr])
        gv2, gr2 = self.grid(self.vec, 36), self.grid(self.rr, 36)
        va, rb = self.vec[None, :] >= gv2[:, None], self.rr[None, :] >= gr2[:, None]
        params = [{"vec": float(a), "rr": float(b)} for a in gv2 for b in gr2]
        out["and"] = ((va[:, None, :] & rb[None, :, :]).reshape(-1, len(self.vec)), params)
        out["or"] = ((va[:, None, :] | rb[None, :, :]).reshape(-1, len(self.vec)), params)
        zv = (self.vec - self.vec[train].mean()) / self.vec[train].std()
        zr = (self.rr - self.rr[train].mean()) / self.rr[train].std()
        mats, params = [], []
        for w in (0.25, 0.5, 1.0, 2.0):
            s = zv + w * zr
            g = self.grid(s, 60)
            mats.append(s[None, :] >= g[:, None])
            params += [{"w": w, "t": float(t)} for t in g]
        out["zsum"] = (np.vstack(mats), params)
        return out


def pick(P: np.ndarray, hit: np.ndarray, ans: np.ndarray, mask: np.ndarray) -> int:
    """Index of the gate setting with the best J = 0.5 hit + 0.5 refused on `mask` (ties: the middle one)."""
    a, o = mask & ans, mask & ~ans
    h = (P[:, a] & hit[a]).mean(axis=1)
    f = (~P[:, o]).mean(axis=1)
    j = 0.5 * h + 0.5 * f
    tied = np.flatnonzero(j >= j.max() - 1e-12)
    return int(tied[len(tied) // 2])


def cross_validate(gates: Gates, name: str, hit: np.ndarray, ans: np.ndarray):
    """Out-of-fold pass vector, plus the setting chosen on all questions (what would be configured)."""
    oof = np.zeros(len(ans), bool)
    for f in range(FOLDS):
        train = gates.folds != f
        P, _ = gates.matrices(train)[name]
        k = pick(P, hit, ans, train)
        oof[~train] = P[k, ~train]
    P, params = gates.matrices(np.ones(len(ans), bool))[name]
    return oof, params[pick(P, hit, ans, np.ones(len(ans), bool))]


# ------------------------------------------------------------------ scoring
def scorecard(recs, outs, passed, ms) -> dict:
    a = [(o, p) for r, o, p in zip(recs, outs, passed) if r["kind"] == "answerable"]
    off = [p for r, p in zip(recs, passed) if r["kind"] == "offtopic"]
    served = [o for o, p in a if p]
    return {
        "hit": mean(p and o["hit"] for o, p in a), "recall": mean(o["recall"] if p else 0.0 for o, p in a),
        "mrr": mean(o["mrr"] if p else 0.0 for o, p in a), "refused": mean(not p for p in off),
        "prec": mean(o["prec"] for o in served), "chunks": mean(o["n"] for o in served), "ms": ms,
    }


def goals_met(s: dict) -> int:
    return sum(s[k] >= v - 1e-9 for k, v in GOALS.items()) + (s["ms"] <= LATENCY_BUDGET_MS)


def rank_key(s: dict):
    return (goals_met(s), s["hit"] + s["refused"] + s["prec"])


def fmt(s: dict) -> str:
    return (f"{s['hit']:>6.3f} {s['recall']:>6.3f} {s['mrr']:>6.3f} {s['refused']:>7.3f} {s['prec']:>6.3f} "
            f"{s['chunks']:>6.2f} {s['ms']:>6.0f} {goals_met(s):>5}/4")


HEADER = f"{'pipeline':<58} {'hit':>6} {'R@5':>6} {'MRR':>6} {'refused':>7} {'prec':>6} {'chunks':>6} {'ms':>6} {'goals':>7}"


def make_folds(recs) -> np.ndarray:
    idx = sorted(range(len(recs)), key=lambda i: recs[i]["q"])
    random.Random(7).shuffle(idx)
    folds = np.zeros(len(recs), int)
    for kind in ("answerable", "offtopic"):  # stratified: each fold gets its share of off-topic questions
        for j, i in enumerate([i for i in idx if recs[i]["kind"] == kind]):
            folds[i] = j % FOLDS
    return folds


def current_pipeline(recs, models, ans, folds):
    """The shipped pipeline: MiniLM-L-6 over every candidate, rerank order, chunk threshold (CV-tuned)."""
    sc = models[RECORDED]["scores"]
    lists = [by_rerank([replace(c, rerank_score=sc[r["q"]][c.chunk_id]) for c in r["_c"]]) for r in recs]
    grid = np.round(np.arange(-9.0, -2.0, 0.05), 2)
    outs = [[outcome(r, select_context(l, t, TOP_K, BUDGET)) | {"ctx": bool(select_context(l, t, TOP_K, BUDGET))}
             for r, l in zip(recs, lists)] for t in grid]
    hit = np.array([[o.get("hit", False) for o in row] for row in outs])
    ctx = np.array([[o["ctx"] for o in row] for row in outs])

    def best(mask):
        a, o = mask & ans, mask & ~ans
        j = 0.5 * hit[:, a].mean(axis=1) + 0.5 * (~ctx[:, o]).mean(axis=1)
        tied = np.flatnonzero(j >= j.max() - 1e-12)
        return int(tied[len(tied) // 2])

    oof_k = np.zeros(len(recs), int)
    for f in range(FOLDS):
        oof_k[folds == f] = best(folds != f)
    chosen = [outs[oof_k[i]][i] for i in range(len(recs))]
    passed = [o["ctx"] for o in chosen]
    n_all = mean(len(r["_c"]) for r in recs)
    s = scorecard(recs, chosen, passed, BASE_MS + n_all * models[RECORDED]["ms_per_passage"])
    return s, float(grid[best(np.ones(len(recs), bool))]), np.array(passed), chosen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(RECORDS.with_name("strategies_result.json")))
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    blob, recs, models = load()
    ans = np.array([r["kind"] == "answerable" for r in recs])
    folds = make_folds(recs)
    vec_top = np.array([max(v for v in r["_vec"].values() if v is not None) for r in recs])
    print(f"{ans.sum()} answerable + {(~ans).sum()} off-topic; {FOLDS}-fold CV, every number out-of-fold; "
          f"models: {', '.join(models)}")

    base, base_t, base_pass, base_outs = current_pipeline(recs, models, ans, folds)
    rows = [{"label": f"CURRENT  MiniLM-L-6 all, rerank order, chunk thr (cfg {base_t})", "s": base,
             "uses_rr": True, "per_q": (base_outs, base_pass), "setting": {"chunk_threshold": base_t}}]

    filters = [("none", 0)] + [("topk", k) for k in (4, 3)] + [("rel_rr", d) for d in (3.0, 2.5, 2.0, 1.5)] + \
              [("rel_vec", d) for d in (0.04, 0.03, 0.02, 0.015)] + [("rel_vec_bm", d) for d in (0.03, 0.02)]
    orders = [("rrf", 0)] + [("rerank", 0)] + [("rrf3", w) for w in (0.5, 1.0, 2.0)]

    # reranker-free pipelines: RRF order, vector gate, non-rerank filters
    g0 = Gates(vec_top, None, folds)
    for fk, fp in [f for f in filters if f[0] != "rel_rr"]:
        outs = []
        for r in recs:
            kept, k = apply_filter(fk, fp, r["_rrf"], r, 1.0)
            outs.append(outcome(r, select_context(kept, -INF, k, BUDGET)))
        hit = np.array([o.get("hit", False) for o in outs])
        passed, setting = cross_validate(g0, "vec", hit, ans)
        rows.append({"label": f"NO RERANKER  rrf | {fk} {fp} | gate vec", "s": scorecard(recs, outs, passed, BASE_MS),
                     "uses_rr": False, "per_q": (outs, passed), "setting": setting})

    for mname, mb in models.items():
        for n in (10, 5):
            if mname == RECORDED and n == 5:
                continue
            ms = BASE_MS + n * mb["ms_per_passage"]
            lists = [scored_list(r, mb["scores"][r["q"]], n) for r in recs]
            rr_top = np.array([max(c.rerank_score for c in l) for l in lists])
            gates = Gates(vec_top, rr_top, folds)
            for ok, ow in orders:
                ordered = [order(ok, ow, l) for l in lists]
                for fk, fp in filters:
                    outs = []
                    for r, l in zip(recs, ordered):
                        kept, k = apply_filter(fk, fp, l, r, mb["sigma"])
                        outs.append(outcome(r, select_context(kept, -INF, k, BUDGET)))
                    hit = np.array([o.get("hit", False) for o in outs])
                    for gname in ("vec", "rr", "and", "or", "zsum"):
                        uses_rr = gname != "vec" or ok != "rrf" or fk == "rel_rr"
                        if not uses_rr:
                            continue  # identical to a reranker-free row above
                        passed, setting = cross_validate(gates, gname, hit, ans)
                        o_lbl = ok + (f" w{ow}" if ok == "rrf3" else "")
                        rows.append({"label": f"{mname} top{n} | {o_lbl} | {fk} {fp} | gate {gname}",
                                     "s": scorecard(recs, outs, passed, ms), "uses_rr": True,
                                     "per_q": (outs, passed), "setting": setting})
        print(f"  scored {mname}", flush=True)

    rows.sort(key=lambda x: rank_key(x["s"]), reverse=True)
    print(f"\ngoals: hit >= {GOALS['hit']}, refused >= {GOALS['refused']}, prec >= {GOALS['prec']}, "
          f"time <= {LATENCY_BUDGET_MS} ms (est.); ranked by goals met, then hit + refused + prec\n")
    print(HEADER)
    print(f"{rows_label(base_row := next(x for x in rows if x['label'].startswith('CURRENT'))):<58} {fmt(base_row['s'])}")
    print("-" * len(HEADER))
    for x in rows[: args.top]:
        print(f"{rows_label(x):<58} {fmt(x['s'])}")

    best_free = next(x for x in rows if not x["uses_rr"])
    best_rr = next(x for x in rows if x["uses_rr"] and not x["label"].startswith("CURRENT"))
    print("\nbest per reranker model:")
    for mname in models:
        x = next((x for x in rows if x["label"].startswith(mname + " ")), None)
        if x:
            print(f"  {rows_label(x):<56} {fmt(x['s'])}")
    print(f"\nbest without a reranker : {best_free['label']}  setting {best_free['setting']}")
    print(f"best with a reranker    : {best_rr['label']}  setting {best_rr['setting']}")
    d_hit, d_prec = best_rr["s"]["hit"] - best_free["s"]["hit"], best_rr["s"]["prec"] - best_free["s"]["prec"]
    print(f"reranker advantage      : hit {d_hit:+.3f}, prec {d_prec:+.3f}, refused "
          f"{best_rr['s']['refused'] - best_free['s']['refused']:+.3f}  (rule: keep it only if >= +0.02 on hit or "
          f"prec and within {LATENCY_BUDGET_MS} ms)")

    # paired bootstrap vs current, and per tag
    rng = random.Random(1)
    qa = [i for i in range(len(recs)) if ans[i]]
    qo = [i for i in range(len(recs)) if not ans[i]]

    def per_q(x):
        outs, passed = x["per_q"]
        return [bool(passed[i]) and outs[i].get("hit", False) if ans[i] else not passed[i] for i in range(len(recs))]

    bq = per_q(base_row)
    picks = rows[:5] + [x for x in (best_free, best_rr) if x not in rows[:5]]
    print("\npaired bootstrap vs current (2000 resamples; median [5%, 95%]):")
    for x in picks:
        xq = per_q(x)
        dh, df = [], []
        for _ in range(2000):
            sa = [rng.choice(qa) for _ in qa]
            so = [rng.choice(qo) for _ in qo]
            dh.append(mean(xq[i] for i in sa) - mean(bq[i] for i in sa))
            df.append(mean(xq[i] for i in so) - mean(bq[i] for i in so))
        print(f"  {rows_label(x):<56} hit {np.median(dh):+.3f} [{np.percentile(dh, 5):+.3f}, {np.percentile(dh, 95):+.3f}]"
              f"  refused {np.median(df):+.3f} [{np.percentile(df, 5):+.3f}, {np.percentile(df, 95):+.3f}]")

    tags = sorted({r["tag"] for r in recs if r["kind"] == "answerable"})
    cols = [base_row] + picks[:4]
    print(f"\nhit per tag:  {'tag':<10} {'n':>3} " + " ".join(f"{'#' + str(i):>6}" for i in range(len(cols))))
    for i, x in enumerate(cols):
        print(f"   #{i} = {rows_label(x)}")
    for t in tags:
        idx = [i for i in qa if recs[i]["tag"] == t]
        vals = [mean(per_q(x)[i] for i in idx) for x in cols]
        print(f"              {t:<10} {len(idx):>3} " + " ".join(f"{v:>6.2f}" for v in vals))

    out = [{"label": x["label"], "uses_reranker": x["uses_rr"], "setting": x["setting"], **x["s"]} for x in rows]
    Path(args.json).write_text(json.dumps({"current": base, "rows": out}, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} pipelines; saved {args.json}")
    return 0


def rows_label(x) -> str:
    return x["label"] if len(x["label"]) <= 58 else x["label"][:55] + "..."


if __name__ == "__main__":
    sys.exit(main())
