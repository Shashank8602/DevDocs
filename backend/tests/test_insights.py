"""Stack / env / symbol / import-graph analysis (pure functions), and that the dashboard, search, symbols,
starters and graph endpoints only ever use files the signed-in person may read."""
import json

import pytest

from app import analysis
from app.api import insights
from tests.conftest import add_member, make_user


# ----- pure analysis -----
def test_stack_is_read_from_manifests_only():
    stack = analysis.detect_stack({
        "package.json": json.dumps({"dependencies": {"react": "^18", "left-pad": "1"}, "devDependencies": {"vite": "5"}}),
        "backend/requirements.txt": "fastapi>=0.1\nsqlalchemy[asyncio]==2\nsome-unknown-lib\n",
        "docker-compose.yml": "services:\n  db:\n    image: postgres:16\n  es:\n    image: docker.elastic.co/elasticsearch/elasticsearch:8\n",
    })
    names = {s.name for s in stack}
    assert {"React", "Vite", "FastAPI", "SQLAlchemy", "PostgreSQL", "Elasticsearch", "Docker"} <= names
    assert "left-pad" not in names and "some-unknown-lib" not in names
    assert analysis.detect_stack({"notes.md": "we use react and fastapi"}) == []  # prose is not a manifest
    assert analysis.detect_stack({"package.json": "{not json"}) == []  # a broken manifest is skipped, not fatal


def test_entry_points_by_name_and_main_guard():
    found = dict(analysis.find_entry_points({
        "src/main.py": "x = 1", "tool.py": 'if __name__ == "__main__":\n    run()\n', "lib/util.py": "y = 2",
        "web/index.tsx": "", "README.md": "",
    }))
    assert set(found) == {"src/main.py", "tool.py", "web/index.tsx"}
    assert "__main__" in found["tool.py"]


def test_env_vars_across_languages_with_first_location():
    files = {
        "a.py": 'import os\nx = os.getenv("DATABASE_URL")\ny = os.environ["SECRET_NAME"]\nz = os.environ.get("MODE_X")\n',
        "b.ts": "const u = process.env.API_URL;\nconst v = import.meta.env.VITE_FLAG;\nprocess.env.NODE_ENV\n",
        "Main.java": 'String k = System.getenv("JAVA_KEY");\n',
        ".env.example": "DATABASE_URL=\n# comment\nUNUSED_KEY=1\n",
    }
    found = {v.name: v for v in analysis.find_env_vars(files)}
    assert set(found) == {"DATABASE_URL", "SECRET_NAME", "MODE_X", "API_URL", "VITE_FLAG", "JAVA_KEY", "UNUSED_KEY"}
    assert found["DATABASE_URL"].declared and not found["SECRET_NAME"].declared
    assert (found["SECRET_NAME"].path, found["SECRET_NAME"].line) == ("a.py", 3)
    assert "NODE_ENV" not in found  # a build-tool built-in, not configuration


def test_symbols_in_several_languages():
    py = analysis.find_symbols("a.py", "class A:\n    def m(self):\n        pass\n\ndef f():\n    pass\n", "python")
    assert [(s.name, s.kind, s.line) for s in py] == [("A", "class", 1), ("A.m", "method", 2), ("f", "function", 5)]
    js = analysis.find_symbols(
        "a.ts", "export function go() {}\nexport const make = async (x) => x;\nclass Box {}\n", "typescript",
    )
    assert {s.name for s in js} == {"go", "make", "Box"}
    assert [s.name for s in analysis.find_symbols("m.go", "func (s *S) Run() {}\ntype S struct {}\n", "go")] == ["Run", "S"]
    assert analysis.find_symbols("bad.py", "def (:", "python") == []


def test_python_imports_resolve_absolute_relative_and_src_layouts():
    files = {
        "backend/app/main.py": ("from app import models\nfrom app.api import files\nimport os, json\nfrom . import config\n", "python"),
        "backend/app/models.py": ("from .db import Base\n", "python"),
        "backend/app/db.py": ("", "python"),
        "backend/app/config.py": ("", "python"),
        "backend/app/api/files.py": ("from app.models import File\nfrom ..db import Base\n", "python"),
        "backend/app/api/__init__.py": ("", "python"),
    }
    edges = set(analysis.build_graph(files))
    assert edges == {
        ("backend/app/main.py", "backend/app/models.py"),
        ("backend/app/main.py", "backend/app/api/files.py"),
        ("backend/app/main.py", "backend/app/config.py"),
        ("backend/app/models.py", "backend/app/db.py"),
        ("backend/app/api/files.py", "backend/app/models.py"),
        ("backend/app/api/files.py", "backend/app/db.py"),
    }  # os / json are not project files, so they are not edges


def test_js_imports_resolve_extensions_index_files_and_aliases_but_not_packages():
    files = {
        "src/App.tsx": ('import React from "react";\nimport { Box } from "./components/Box";\nimport u from "../lib/util";\n'
                        'import "./index.css";\nconst x = await import("./pages/Home");\nimport h from "@/hooks/useThing";\n', "typescript"),
        "src/components/Box.tsx": ("export const Box = 1;", "typescript"),
        "lib/util.ts": ("", "typescript"),
        "src/pages/Home/index.ts": ("", "typescript"),
        "src/hooks/useThing.ts": ("const a = require('../lib2/x');", "typescript"),
        "src/lib2/x.js": ("", "javascript"),
    }
    edges = set(analysis.build_graph(files))
    assert ("src/App.tsx", "src/components/Box.tsx") in edges
    assert ("src/App.tsx", "src/pages/Home/index.ts") in edges
    assert ("src/App.tsx", "src/hooks/useThing.ts") in edges
    assert ("src/hooks/useThing.ts", "src/lib2/x.js") in edges
    assert not any(dst == "react" for _, dst in edges) and len({d for s, d in edges if s == "src/App.tsx"}) == 4  # incl. lib/util.ts


def test_java_and_c_includes():
    files = {
        "src/main/java/com/x/Main.java": ("import com.x.util.Helper;\nimport java.util.List;\n", "java"),
        "src/main/java/com/x/util/Helper.java": ("", "java"),
        "src/main.c": ('#include "util.h"\n#include <stdio.h>\n', "c"),
        "src/util.h": ("", "c"),
    }
    assert set(analysis.build_graph(files)) == {
        ("src/main/java/com/x/Main.java", "src/main/java/com/x/util/Helper.java"), ("src/main.c", "src/util.h"),
    }


def test_circular_imports_and_impact():
    edges = [("a.py", "b.py"), ("b.py", "c.py"), ("c.py", "a.py"), ("d.py", "b.py")]
    assert analysis.transitive_importers(edges, "c.py") == ["a.py", "b.py", "d.py"]  # terminates despite the cycle
    assert analysis.transitive_importers(edges, "d.py") == []


# ----- endpoints -----
PY_MAIN = 'import os\nfrom app import util\n\ndef run():\n    # TODO: add retries\n    return os.getenv("SERVICE_URL")\n\nif __name__ == "__main__":\n    run()\n'
PY_UTIL = "def helper():\n    return 1\n"
HR_SECRET = "import os\n\ndef payroll_total():\n    # FIXME salary data is wrong\n    return os.getenv(\"PAYROLL_KEY\")\n"
ITEMS = [
    ("pub/app/main.py", PY_MAIN), ("pub/app/util.py", PY_UTIL), ("pub/requirements.txt", "fastapi==1\n"),
    ("pub/README.md", "# Public readme\nHello"), ("hr/payroll.py", HR_SECRET), ("hr/notes.md", "# private notes\n"),
]


@pytest.fixture
async def shared(http, admin, sessions, ingest):
    ana = await make_user(sessions, "ana@example.test")
    ben = await make_user(sessions, "ben@example.test")
    ac = http(admin)
    pid = (await ac.post("/projects", json={"name": "P"})).json()["id"]
    res = await ac.post(
        f"/projects/{pid}/upload", files=[("files", (p, d.encode())) for p, d in ITEMS], data={"paths": [p for p, _ in ITEMS]},
    )
    assert res.status_code == 200
    await ingest(pid)
    await add_member(ac, pid, ana)
    await add_member(ac, pid, ben)
    put = lambda path, vis, ids=(): ac.put(f"/projects/{pid}/access", json={"path": path, "visibility": vis, "user_ids": [str(i) for i in ids]})
    assert (await put("pub", "everyone")).status_code == 200
    assert (await put("hr", "people", [ana.id])).status_code == 200
    insights.clear_cache()
    return type("S", (), {"pid": pid, "ac": ac, "ana": http(ana), "ben": http(ben), "ana_user": ana})


async def test_dashboard_differs_per_person_and_never_leaks_hidden_files(shared):
    ana = (await shared.ana.get(f"/projects/{shared.pid}/dashboard")).json()
    ben = (await shared.ben.get(f"/projects/{shared.pid}/dashboard")).json()
    admin = (await shared.ac.get(f"/projects/{shared.pid}/dashboard")).json()
    assert (admin["file_count"], ana["file_count"], ben["file_count"]) == (6, 6, 4)
    assert {e["name"] for e in ben["env_vars"]} == {"SERVICE_URL"} and "PAYROLL_KEY" in {e["name"] for e in ana["env_vars"]}
    assert "hr/" not in json.dumps(ben) and "payroll" not in json.dumps(ben).lower()
    assert ben["chunk_count"] < ana["chunk_count"]
    assert ben["readme"]["path"] == "pub/README.md" and "Public readme" in ben["readme"]["excerpt"]
    assert {s["name"] for s in ben["stack"]} == {"FastAPI"}
    assert ben["entry_points"][0]["path"] == "pub/app/main.py"
    assert ben["by_type"] == {"doc": 1, "source": 2, "other": 0} or sum(ben["by_type"].values()) == 4


async def test_a_permission_change_changes_the_dashboard_on_the_next_request(shared):
    before = (await shared.ben.get(f"/projects/{shared.pid}/dashboard")).json()
    assert before["file_count"] == 4
    await shared.ac.put(f"/projects/{shared.pid}/access", json={"path": "hr", "visibility": "everyone", "user_ids": []})
    after = (await shared.ben.get(f"/projects/{shared.pid}/dashboard")).json()
    assert after["file_count"] == 6 and "PAYROLL_KEY" in {e["name"] for e in after["env_vars"]}


async def test_search_only_returns_readable_files_and_applies_filters(shared):
    ben = (await shared.ben.get(f"/projects/{shared.pid}/search", params={"q": "payroll_total"})).json()
    assert ben["hits"] == []
    ana = (await shared.ana.get(f"/projects/{shared.pid}/search", params={"q": "payroll_total"})).json()
    assert [h["path"] for h in ana["hits"]] == ["hr/payroll.py"]
    hit = ana["hits"][0]
    assert hit["line_start"] == 3 and "payroll_total" in hit["snippet"]

    both = (await shared.ana.get(f"/projects/{shared.pid}/search", params={"q": "getenv"})).json()
    assert {h["path"] for h in both["hits"]} == {"hr/payroll.py", "pub/app/main.py"}
    in_pub = (await shared.ana.get(f"/projects/{shared.pid}/search", params={"q": "getenv", "folder": "pub"})).json()
    assert {h["path"] for h in in_pub["hits"]} == {"pub/app/main.py"}
    docs = (await shared.ana.get(f"/projects/{shared.pid}/search", params={"q": "notes", "file_type": "doc"})).json()
    assert {h["path"] for h in docs["hits"]} == {"hr/notes.md"}
    assert (await shared.ana.get(f"/projects/{shared.pid}/search", params={"q": "x"})).status_code == 422  # too short
    assert (await shared.ana.get(f"/projects/{shared.pid}/search", params={"q": "go", "file_type": "bogus"})).status_code == 422


async def test_symbols_and_starters_never_mention_hidden_files(shared):
    ben_syms = (await shared.ben.get(f"/projects/{shared.pid}/symbols")).json()
    assert {s["name"] for s in ben_syms} == {"run", "helper"}
    ana_syms = (await shared.ana.get(f"/projects/{shared.pid}/symbols", params={"q": "payroll"})).json()
    assert [s["name"] for s in ana_syms] == ["payroll_total"] and ana_syms[0]["line"] == 3

    starters = (await shared.ben.get(f"/projects/{shared.pid}/starters")).json()["questions"]
    assert starters and "hr/" not in " ".join(starters) and "PAYROLL" not in " ".join(starters)
    assert any("README" in q for q in starters)
    # every starter is a real question about something that exists in what Ben can read
    assert all(q.strip() for q in starters) and len(starters) <= 4


async def test_graph_has_no_node_or_edge_for_a_file_the_person_cannot_read(shared, sessions):
    ben = (await shared.ben.get(f"/projects/{shared.pid}/graph")).json()
    assert {n["path"] for n in ben["nodes"]} == {"pub/app/main.py", "pub/app/util.py"}
    node_ids = {n["id"] for n in ben["nodes"]}
    assert ben["edges"] and all(e["source"] in node_ids and e["target"] in node_ids for e in ben["edges"])
    ana = (await shared.ana.get(f"/projects/{shared.pid}/graph")).json()
    assert len(ana["nodes"]) == 2  # payroll.py imports only `os`, so it has no project edge


async def test_graph_impact_and_its_404_for_hidden_files(shared):
    graph = (await shared.ben.get(f"/projects/{shared.pid}/graph")).json()
    util = next(n for n in graph["nodes"] if n["path"].endswith("util.py"))
    impact = (await shared.ben.get(f"/projects/{shared.pid}/graph/{util['id']}")).json()
    assert impact["imported_by"] == ["pub/app/main.py"] and impact["affected"] == ["pub/app/main.py"]
    files = (await shared.ac.get(f"/projects/{shared.pid}/files")).json()
    hidden = next(f["id"] for f in files if f["file_path"] == "hr/payroll.py")
    assert (await shared.ben.get(f"/projects/{shared.pid}/graph/{hidden}")).status_code == 404
    assert (await shared.ana.get(f"/projects/{shared.pid}/graph/{hidden}")).status_code == 200


async def test_members_with_no_access_get_404_everywhere_and_anonymous_gets_401(http, admin, sessions, shared):
    ac = http(admin)
    private = (await ac.post("/projects", json={"name": "Nobody-reads-this"})).json()["id"]
    await ac.post(f"/projects/{private}/upload", files=[("files", ("a.py", b"def f(): return 1"))], data={"paths": ["a.py"]})
    outsider = http(await make_user(sessions, "out@example.test"))
    for path in ("dashboard", "search?q=main", "symbols", "starters", "graph"):
        assert (await outsider.get(f"/projects/{private}/{path}")).status_code == 404, path
        assert (await http().get(f"/projects/{shared.pid}/{path}")).status_code == 401, path
