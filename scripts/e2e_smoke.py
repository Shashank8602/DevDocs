"""End-to-end smoke test against a RUNNING backend with real Postgres, Elasticsearch and the real providers.

    python scripts/e2e_smoke.py --base http://127.0.0.1:8010 [--project samples/ecommerce-demo] [--no-chat]

Run it against a throwaway database and data directory, never your real one: it creates the first admin account
and several people. It uses a few embedding calls and (unless --no-chat) a few chat calls. See
Docs/Reports/Gaps-Implementation.md for how to start the isolated backend.

Every check prints PASS or FAIL; the exit code is the number of failures."""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)
    return ok


def sse(client: httpx.Client, url: str, body: dict, headers: dict | None = None) -> list[tuple[str, dict]]:
    out = []
    with client.stream("POST", url, json=body, headers=headers or {}, timeout=180) as res:
        if res.status_code != 200:
            return [("http_error", {"status": res.status_code})]
        text = "".join(res.iter_text())
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n", 1)
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--project", default=str(Path(__file__).resolve().parents[1] / "samples" / "ecommerce-demo"))
    ap.add_argument("--no-chat", action="store_true", help="skip the steps that call the chat model")
    args = ap.parse_args()
    root = Path(args.project)

    anon = httpx.Client(base_url=args.base, timeout=60)
    admin, ana, ben = (httpx.Client(base_url=args.base, timeout=60) for _ in range(3))
    pw = "e2e-password-123"

    # --- identity ---
    st = anon.get("/auth/status").json()
    check("fresh database asks for first-run setup", st["needs_setup"] is True, str(st))
    check("anonymous request is 401", anon.get("/projects").status_code == 401)
    r = admin.post("/auth/setup", json={"email": "admin@e2e.test", "password": pw, "name": "Admin"})
    check("first-run setup creates the admin", r.status_code == 201 and r.json()["role"] == "admin", r.text)
    check("second setup is refused", anon.post("/auth/setup", json={"email": "x@e2e.test", "password": pw}).status_code == 409)
    check("session cookie keeps the admin signed in", admin.get("/auth/me").json()["email"] == "admin@e2e.test")
    for email in ("ana@e2e.test", "ben@e2e.test"):
        r = admin.post("/users", json={"email": email, "password": pw, "name": email.split("@")[0], "role": "member"})
        check(f"admin creates {email}", r.status_code == 201, r.text)
    ana_id = next(u["id"] for u in admin.get("/users").json() if u["email"] == "ana@e2e.test")
    ben_id = next(u["id"] for u in admin.get("/users").json() if u["email"] == "ben@e2e.test")
    for c, email in ((ana, "ana@e2e.test"), (ben, "ben@e2e.test")):
        check(f"{email} signs in", c.post("/auth/login", json={"email": email, "password": pw}).status_code == 200)
    check("member cannot create a project", ana.post("/projects", json={"name": "nope"}).status_code == 403)
    check("member cannot open Settings", ana.get("/settings").status_code == 403)
    check("wrong password is 401", anon.post("/auth/login", json={"email": "ana@e2e.test", "password": "wrong-wrong"}).status_code == 401)

    # --- upload (the sample has node_modules and a .env: both must be skipped) ---
    pid = admin.post("/projects", json={"name": "E2E sample"}).json()["id"]
    items = [(p.relative_to(root).as_posix(), p.read_bytes()) for p in sorted(root.rglob("*")) if p.is_file()]
    items.append(("backend/leaky.py", b"AWS_KEY = 'AKIAABCDEFGHIJKLMNOP'\n"))
    r = admin.post(f"/projects/{pid}/upload", files=[("files", (p, d)) for p, d in items], data={"paths": [p for p, _ in items]})
    check("upload accepted", r.status_code == 200, r.text[:200])
    report = r.json()
    rejected = {x["path"]: x["reason"] for x in report["results"] if x["status"] == "rejected"}
    check("node_modules and .env are skipped", any("node_modules" in p for p in rejected) and ".env" in rejected, str(rejected))
    check("a file with a credential is skipped and named", rejected.get("backend/leaky.py") == "possible_secret", str(rejected))
    check("the secret itself is never echoed", "AKIAABCDEFGHIJKLMNOP" not in r.text)

    # --- ingestion ---
    deadline, files = time.time() + 240, []
    while time.time() < deadline:
        proj = admin.get(f"/projects/{pid}").json()
        files = admin.get(f"/projects/{pid}/files").json()
        if proj["status"] in ("ready", "failed", "needs_reindex") or proj.get("paused_until"):
            break
        time.sleep(2)
    statuses = {f["file_path"]: f["status"] for f in files}
    print("      statuses:", statuses)
    paused = bool(proj.get("paused_until"))
    check("ingestion finished or paused for quota (never silently failed)", proj["status"] == "ready" or paused,
          f"{proj['status']} {statuses}")
    if paused:
        print("      NOTE: embedding quota is spent; the rest of the run is limited to what was indexed")
    ready = [p for p, s in statuses.items() if s == "ready"]
    check("some files are ready", len(ready) > 0, str(statuses))
    check("a corrupt PDF fails only itself", statuses.get("docs/corrupt.pdf") in ("failed", None) and len(ready) > 1, str(statuses))

    # --- access rules ---
    def rule(path, vis, ids=()):
        return admin.put(f"/projects/{pid}/access", json={"path": path, "visibility": vis, "user_ids": list(ids)})

    check("a person outside the project cannot see it", ana.get(f"/projects/{pid}").status_code == 404)
    for uid, who in ((ana_id, "ana"), (ben_id, "ben")):
        check(f"admin adds {who} to this project", admin.post(f"/projects/{pid}/members", json={"email": f"{who}@e2e.test"}).status_code == 201)
    check("a rule can only name project members", rule("backend", "people", ["00000000-0000-0000-0000-000000000001"]).status_code == 422)
    check("rule: docs and frontend for everyone", rule("docs", "everyone").status_code == 200 and rule("frontend", "everyone").status_code == 200)
    check("rule: backend for ana only", rule("backend", "people", [ana_id]).status_code == 200)
    ana_files = {f["file_path"] for f in ana.get(f"/projects/{pid}/files").json()}
    ben_files = {f["file_path"] for f in ben.get(f"/projects/{pid}/files").json()}
    check("ana sees backend files", any(p.startswith("backend/") for p in ana_files), str(ana_files))
    check("ben sees no backend file, no README (no rule), no dataset", not any(p.startswith(("backend/", "datasets/")) or p == "README.md" for p in ben_files), str(ben_files))
    check("ben's project counts match his files", ben.get(f"/projects/{pid}").json()["file_count"] == len(ben_files))
    backend_id = next(f["id"] for f in admin.get(f"/projects/{pid}/files").json() if f["file_path"] == "backend/auth.py")
    check("restricted file content is 404 for ben", ben.get(f"/files/{backend_id}/content").status_code == 404)
    check("restricted file content is 200 for ana", ana.get(f"/files/{backend_id}/content").status_code == 200)

    # --- insights (no AI) ---
    dash_a, dash_b = ana.get(f"/projects/{pid}/dashboard").json(), ben.get(f"/projects/{pid}/dashboard").json()
    check("dashboard differs per person", dash_a["file_count"] > dash_b["file_count"], f"{dash_a['file_count']} vs {dash_b['file_count']}")
    check("ben's dashboard names nothing from backend/", "backend/" not in json.dumps(dash_b))
    hits_a = ana.get(f"/projects/{pid}/search", params={"q": "payment"}).json()
    hits_b = ben.get(f"/projects/{pid}/search", params={"q": "payment"}).json()
    check("search finds backend code for ana", any(h["path"].startswith("backend/") for h in hits_a["hits"]), str(hits_a)[:200])
    check("search returns nothing from backend for ben", not any(h["path"].startswith("backend/") for h in hits_b["hits"]))
    check("symbols and starters work", ana.get(f"/projects/{pid}/symbols").status_code == 200 and ana.get(f"/projects/{pid}/starters").status_code == 200)
    graph_b = ben.get(f"/projects/{pid}/graph").json()
    check("graph has no backend node for ben", not any(n["path"].startswith("backend/") for n in graph_b["nodes"]))

    # --- chat, refusal and the audit log ---
    if not args.no_chat and ready:
        q = "How does authentication work and where is the token validated?"
        ev_a = sse(ana, f"/projects/{pid}/conversations", {"content": q})
        names_a = [n for n, _ in ev_a]
        check("ana gets an answer with citations", names_a[-2:] == ["sources", "done"] or "no_context" in names_a, str(names_a))
        src_a = next((d["sources"] for n, d in ev_a if n == "sources"), [])
        check("ana's citations point at backend files", any(s["file_path"].startswith("backend/") for s in src_a), str(src_a))
        ev_b = sse(ben, f"/projects/{pid}/conversations", {"content": q})
        names_b = [n for n, _ in ev_b]
        src_b = next((d["sources"] for n, d in ev_b if n == "sources"), [])
        check("ben never gets a backend citation", not any(s["file_path"].startswith("backend/") for s in src_b), str(src_b))
        off = sse(ben, f"/projects/{pid}/conversations", {"content": "best beaches in goa"})
        refused_b = [d.get("content") for n, d in ev_b if n == "no_context"]
        refused_off = [d.get("content") for n, d in off if n == "no_context"]
        if refused_b and refused_off:
            check("restricted and off-topic refusals are identical", refused_b == refused_off)
            check("the refusal never mentions permission", "permission" not in refused_b[0].lower())
        audit = admin.get(f"/projects/{pid}/audit").json()
        check("audit log lists both people's questions", {r["user_email"] for r in audit} >= {"ana@e2e.test", "ben@e2e.test"}, str(audit)[:300])
        check("members cannot read the audit log", ben.get(f"/projects/{pid}/audit").status_code == 403)
        check("preview mode refuses to create chats", admin.post(
            f"/projects/{pid}/conversations", json={"content": "hi"}, headers={"X-Preview-As": ben_id}).status_code == 409)

        # revoke ana: her old citation must stop opening
        cid = next(d["conversation_id"] for n, d in ev_a if n == "conversation")
        rule("backend", "people", [])
        detail = ana.get(f"/conversations/{cid}").json()
        cited = [s for m in detail["messages"] for s in (m["sources_json"] or [])]
        if src_a:
            stubs = [s for s in cited if s.get("available") is False]
            check("after revoking, backend citations become 'Source no longer available' and name nothing",
                  len(stubs) >= 1 and all(s["label"] == "Source no longer available" and s["file_id"] is None for s in stubs)
                  and not any("backend/" in json.dumps(s) for s in cited), str(cited))
        check("after revoking, ana's retrieval is refused (no backend context)",
              not any(s["file_path"].startswith("backend/") for n, d in sse(ana, f"/projects/{pid}/conversations", {"content": q}) if n == "sources" for s in d["sources"]))
        check("another person's conversation is 404", ben.get(f"/conversations/{cid}").status_code == 404)

    # --- preview, sign-out ---
    prev = admin.get(f"/projects/{pid}/files", headers={"X-Preview-As": ben_id}).json()
    check("preview-as shows ben's view", {f["file_path"] for f in prev} == ben_files)
    check("sign-out ends the session", ana.post("/auth/logout").status_code == 204 and (ana.cookies.clear() or ana.get("/projects").status_code == 401))

    print(f"\n{len(FAILS)} failure(s)" if FAILS else "\nall checks passed")
    for name in FAILS:
        print("  -", name)
    return len(FAILS)


if __name__ == "__main__":
    sys.exit(main())
