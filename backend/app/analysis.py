"""Static analysis of a project's files: detected stack, entry points, environment variables,
symbols and the import graph. Pure functions over (path, text): no database, no network, no LLM.

Everything here is computed from files the *caller* already filtered to what the viewer may read, so a hidden
file can never show up in a count, a list or a graph edge. Limits are stated, not hidden: imports are
static (dynamic loading, dependency injection and string-based routing are invisible)."""
import ast
import json
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass, field

# ----- stack -----
# Dependency name -> label. This is a lookup table of well-known libraries, not project data.
KNOWN_PACKAGES: dict[str, str] = {
    "react": "React", "react-dom": "React", "vue": "Vue", "svelte": "Svelte", "next": "Next.js", "nuxt": "Nuxt",
    "@angular/core": "Angular", "express": "Express", "@nestjs/core": "NestJS", "fastify": "Fastify",
    "vite": "Vite", "webpack": "Webpack", "tailwindcss": "Tailwind CSS", "typescript": "TypeScript",
    "@tanstack/react-query": "TanStack Query", "redux": "Redux", "jest": "Jest", "vitest": "Vitest",
    "prisma": "Prisma", "mongoose": "Mongoose", "sequelize": "Sequelize", "graphql": "GraphQL",
    "fastapi": "FastAPI", "django": "Django", "flask": "Flask", "sqlalchemy": "SQLAlchemy", "pydantic": "Pydantic",
    "alembic": "Alembic", "pytest": "pytest", "celery": "Celery", "uvicorn": "Uvicorn", "numpy": "NumPy",
    "pandas": "pandas", "torch": "PyTorch", "tensorflow": "TensorFlow", "scikit-learn": "scikit-learn",
    "httpx": "httpx", "requests": "Requests", "elasticsearch": "Elasticsearch", "psycopg2": "PostgreSQL driver",
    "asyncpg": "PostgreSQL driver", "redis": "Redis", "langchain": "LangChain", "openai": "OpenAI SDK",
    "spring-boot-starter-web": "Spring Boot", "spring-boot-starter": "Spring Boot", "junit": "JUnit",
    "gin-gonic/gin": "Gin", "labstack/echo": "Echo", "gofiber/fiber": "Fiber", "actix-web": "Actix",
    "tokio": "Tokio", "serde": "Serde", "rocket": "Rocket",
}
KNOWN_IMAGES: dict[str, str] = {
    "postgres": "PostgreSQL", "mysql": "MySQL", "mariadb": "MariaDB", "redis": "Redis", "mongo": "MongoDB",
    "elasticsearch": "Elasticsearch", "rabbitmq": "RabbitMQ", "kafka": "Kafka", "nginx": "Nginx",
    "minio": "MinIO",
}
MANIFESTS = frozenset({
    "package.json", "requirements.txt", "pyproject.toml", "pom.xml", "go.mod", "cargo.toml", "build.gradle",
    "docker-compose.yml", "docker-compose.yaml", "dockerfile", "gemfile", "composer.json",
})
_NAME_SPLIT = re.compile(r"[<>=!~\[\];,\s@]")


@dataclass(frozen=True)
class StackItem:
    name: str
    source: str  # the manifest it was read from


def _normalized(token: str) -> str:
    return token.strip().strip("\"'").lower()


def detect_stack(manifests: dict[str, str]) -> list[StackItem]:
    """Technologies named in manifest files (`path -> text`). Each label is reported once."""
    seen: dict[str, StackItem] = {}

    def add(label: str | None, source: str) -> None:
        if label and label not in seen:
            seen[label] = StackItem(label, source)

    for path, text in sorted(manifests.items()):
        name = posixpath.basename(path).lower()
        if name == "package.json":
            try:
                data = json.loads(text)
            except ValueError:
                continue
            deps = {**(data.get("devDependencies") or {}), **(data.get("dependencies") or {})}
            for dep in deps:
                add(KNOWN_PACKAGES.get(dep.lower()), path)
        elif name in ("requirements.txt", "pyproject.toml", "pom.xml", "go.mod", "cargo.toml", "build.gradle",
                      "gemfile", "composer.json"):
            lowered = text.lower()
            for key, label in KNOWN_PACKAGES.items():
                # a dependency appears as a whole word at the start of a line, in quotes, or inside a tag/path
                if re.search(rf"(?:^|[\s\"'/>:]){re.escape(key)}(?:[\s\"'<>=!~\[\];,@/]|$)", lowered, re.M):
                    add(label, path)
        elif name in ("docker-compose.yml", "docker-compose.yaml", "dockerfile"):
            for match in re.finditer(r"(?:image:|FROM)\s+([^\s]+)", text, re.I):
                image = match.group(1).split("/")[-1].split(":")[0].lower()
                add(KNOWN_IMAGES.get(image), path)
            add("Docker", path)
    return sorted(seen.values(), key=lambda s: s.name.lower())


# ----- entry points -----
ENTRY_NAMES = frozenset({
    "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py", "server.py", "index.js", "index.ts",
    "index.tsx", "main.js", "main.ts", "main.tsx", "server.js", "server.ts", "app.js", "app.ts", "program.cs",
    "main.go", "main.rs", "main.java", "application.java", "index.html",
})
_MAIN_GUARD = re.compile(r"""^if\s+__name__\s*==\s*["']__main__["']\s*:""", re.M)


def find_entry_points(files: dict[str, str]) -> list[tuple[str, str]]:
    """(path, why) for files that start the program: well-known names, or a `__main__` guard."""
    out: dict[str, str] = {}
    for path, text in files.items():
        if posixpath.basename(path).lower() in ENTRY_NAMES:
            out[path] = "conventional entry file name"
        if path.endswith(".py") and _MAIN_GUARD.search(text):
            out[path] = 'has an `if __name__ == "__main__"` block'
    return sorted(out.items(), key=lambda kv: (kv[0].count("/"), kv[0]))


# ----- environment variables -----
_ENV_PATTERNS = [
    re.compile(r"""os\.getenv\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""os\.environ(?:\.get)?\s*[\[(]\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""process\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""process\.env\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""),
    re.compile(r"""import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""System\.getenv\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""os\.Getenv\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""env::var\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
    re.compile(r"""Environment\.GetEnvironmentVariable\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""),
]
_DOTENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", re.M)
_BUILTIN_ENV = frozenset({"NODE_ENV", "MODE", "DEV", "PROD", "BASE_URL", "SSR"})


@dataclass(frozen=True)
class EnvVar:
    name: str
    path: str
    line: int
    declared: bool  # listed in a .env.example / .env.sample style file


def find_env_vars(files: dict[str, str]) -> list[EnvVar]:
    """Every environment variable the code reads, with the first place it is read, plus the keys listed in
    .env.example-style files."""
    first: dict[str, EnvVar] = {}
    declared: set[str] = set()
    for path, text in sorted(files.items()):
        base = posixpath.basename(path).lower()
        if base.startswith(".env") and base.endswith((".example", ".sample", ".template", ".dist")):
            for m in _DOTENV_LINE.finditer(text):
                name = m.group(1)
                declared.add(name)
                line = text.count("\n", 0, m.start()) + 1
                first.setdefault(name, EnvVar(name, path, line, True))
            continue
        for pattern in _ENV_PATTERNS:
            for m in pattern.finditer(text):
                name = m.group(1)
                if name in _BUILTIN_ENV:
                    continue
                line = text.count("\n", 0, m.start()) + 1
                if name not in first:
                    first[name] = EnvVar(name, path, line, False)
    return sorted(
        (EnvVar(v.name, v.path, v.line, v.name in declared) for v in first.values()), key=lambda v: v.name,
    )


# ----- symbols -----
@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    path: str
    line: int


_SYMBOL_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    "javascript": [
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)", re.M)),
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)),
        ("function", re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>", re.M)),
    ],
    "java": [
        ("class", re.compile(r"^\s*(?:public\s+|protected\s+|private\s+|abstract\s+|final\s+|static\s+)*(?:class|interface|enum|record)\s+([A-Za-z_]\w*)", re.M)),
        ("method", re.compile(r"^\s*(?:public|protected|private)\s+(?:static\s+|final\s+|abstract\s+|synchronized\s+)*[\w<>\[\], ?]+\s+([A-Za-z_]\w*)\s*\(", re.M)),
    ],
    "go": [
        ("function", re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\(", re.M)),
        ("type", re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)", re.M)),
    ],
    "rust": [
        ("function", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)", re.M)),
        ("type", re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)", re.M)),
    ],
    "csharp": [
        ("class", re.compile(r"^\s*(?:public\s+|internal\s+|private\s+|protected\s+|static\s+|abstract\s+|sealed\s+|partial\s+)*(?:class|interface|struct|enum|record)\s+([A-Za-z_]\w*)", re.M)),
    ],
    "ruby": [("function", re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!]?)", re.M)), ("class", re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)", re.M))],
    "php": [("function", re.compile(r"^\s*(?:public\s+|private\s+|protected\s+|static\s+)*function\s+([A-Za-z_]\w*)", re.M)), ("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+([A-Za-z_]\w*)", re.M))],
    "c": [("function", re.compile(r"^[A-Za-z_][\w\s\*]*?\b([A-Za-z_]\w*)\s*\([^;{)]*\)\s*\{", re.M))],
}
_SYMBOL_PATTERNS["typescript"] = _SYMBOL_PATTERNS["javascript"]
_SYMBOL_PATTERNS["cpp"] = _SYMBOL_PATTERNS["c"]
_C_KEYWORDS = frozenset({"if", "for", "while", "switch", "return", "sizeof", "else"})


def _python_symbols(path: str, text: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    out: list[Symbol] = []

    def walk(nodes, prefix: str) -> None:
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(Symbol(f"{prefix}{node.name}", "method" if prefix else "function", path, node.lineno))
            elif isinstance(node, ast.ClassDef):
                out.append(Symbol(f"{prefix}{node.name}", "class", path, node.lineno))
                walk(node.body, f"{prefix}{node.name}.")

    walk(tree.body, "")
    return out


def find_symbols(path: str, text: str, language: str | None) -> list[Symbol]:
    """Functions, classes and types defined in one file, with their line numbers."""
    if language == "python":
        return _python_symbols(path, text)
    out: list[Symbol] = []
    for kind, pattern in _SYMBOL_PATTERNS.get(language or "", []):
        for m in pattern.finditer(text):
            name = m.group(1)
            if language in ("c", "cpp") and name in _C_KEYWORDS:
                continue
            out.append(Symbol(name, kind, path, text.count("\n", 0, m.start(1)) + 1))
    return sorted({(s.line, s.name): s for s in out}.values(), key=lambda s: s.line)


# ----- import graph -----
_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_JS_IMPORT = [
    re.compile(r"""(?:import|export)\s+(?:type\s+)?[^'"`;]*?\sfrom\s*["']([^"']+)["']"""),
    re.compile(r"""import\s*["']([^"']+)["']"""),
    re.compile(r"""require\(\s*["']([^"']+)["']\s*\)"""),
    re.compile(r"""import\(\s*["']([^"']+)["']\s*\)"""),
]
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+?)(?:\.\*)?\s*;", re.M)
_C_INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
GRAPH_LANGUAGES = frozenset({"python", "javascript", "typescript", "java", "c", "cpp"})


@dataclass(frozen=True)
class RawImport:
    """One import statement as written, before it is matched to a file."""
    kind: str  # python | js | java | c
    target: str
    level: int = 0  # python relative-import depth
    names: tuple[str, ...] = ()  # python `from x import a, b`


def extract_imports(path: str, text: str, language: str | None) -> list[RawImport]:
    if language == "python":
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError, RecursionError):
            return []
        out: list[RawImport] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                out.extend(RawImport("python", a.name) for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                out.append(RawImport("python", node.module or "", node.level, tuple(a.name for a in node.names)))
        return out
    if language in ("javascript", "typescript"):
        return [RawImport("js", m.group(1)) for p in _JS_IMPORT for m in p.finditer(text)]
    if language == "java":
        return [RawImport("java", m.group(1)) for m in _JAVA_IMPORT.finditer(text)]
    if language in ("c", "cpp"):
        return [RawImport("c", m.group(1)) for m in _C_INCLUDE.finditer(text)]
    return []


class _PathIndex:
    """Lookup of project paths by exact path and by trailing segments (src layouts, monorepo prefixes)."""

    def __init__(self, paths: list[str]):
        self.exact = set(paths)
        self.by_suffix: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            parts = p.split("/")
            for i in range(len(parts)):
                self.by_suffix["/".join(parts[i:])].append(p)

    def find(self, candidate: str, near: str) -> str | None:
        """The project file for a path written from some root: exact first, else the unique (or nearest) suffix match."""
        if candidate in self.exact:
            return candidate
        matches = self.by_suffix.get(candidate, [])
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        def shared(p: str) -> int:
            n = 0
            for a, b in zip(p.split("/"), near.split("/")):
                if a != b:
                    break
                n += 1
            return n

        return max(sorted(matches), key=shared)


def _resolve_python(imp: RawImport, importer: str, index: _PathIndex) -> list[str]:
    base: list[str] = []
    if imp.level:
        folders = importer.split("/")[:-1]
        up = imp.level - 1  # `.` is the importer's own folder, `..` its parent, ...
        if up > len(folders):
            return []
        base = folders[: len(folders) - up]
    module = [p for p in imp.target.split(".") if p]

    def locate(candidate: str) -> str | None:
        found = index.find(candidate, importer) if imp.level == 0 else (candidate if candidate in index.exact else None)
        return found if found and found != importer else None

    # `from pkg import mod` names submodules: link those files, not the package's __init__ they pass through
    submodules = [t for t in (locate("/".join(base + module + [n]) + ".py") for n in imp.names) if t]
    if submodules:
        return list(dict.fromkeys(submodules))
    stem = "/".join(base + module)
    if not stem:
        return []
    return [t for t in (locate(stem + ".py"), locate(stem + "/__init__.py")) if t][:1]


def _resolve_js(imp: RawImport, importer: str, index: _PathIndex) -> list[str]:
    spec = imp.target
    if spec.startswith("@/"):
        candidates_root = "src/" + spec[2:]
    elif spec.startswith("."):
        candidates_root = posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec))
    else:
        return []  # a package from node_modules
    forms = [candidates_root] + [candidates_root + e for e in _JS_EXTENSIONS] + [
        f"{candidates_root}/index{e}" for e in _JS_EXTENSIONS
    ]
    for form in forms:
        if form in index.exact and form != importer:
            return [form]
    if spec.startswith("@/"):  # alias root differs from `src/`: fall back to suffix matching
        for form in forms:
            found = index.find(form, importer)
            if found and found != importer:
                return [found]
    return []


def _resolve_java(imp: RawImport, importer: str, index: _PathIndex) -> list[str]:
    found = index.find(imp.target.replace(".", "/") + ".java", importer)
    if found is None and "." in imp.target:  # a nested type or a static member: try the enclosing class
        found = index.find(imp.target.rsplit(".", 1)[0].replace(".", "/") + ".java", importer)
    return [found] if found and found != importer else []


def _resolve_c(imp: RawImport, importer: str, index: _PathIndex) -> list[str]:
    here = posixpath.normpath(posixpath.join(posixpath.dirname(importer), imp.target))
    found = here if here in index.exact else index.find(imp.target, importer)
    return [found] if found and found != importer else []


def build_graph(files: dict[str, tuple[str, str | None]]) -> list[tuple[str, str]]:
    """Edges (importer, imported) among `files` (`path -> (text, language)`). Only imports that resolve to a
    file in `files` become edges; everything else (standard library, packages) is dropped."""
    index = _PathIndex(list(files))
    edges: set[tuple[str, str]] = set()
    for path, (text, language) in files.items():
        if language not in GRAPH_LANGUAGES:
            continue
        for imp in extract_imports(path, text, language):
            resolver = {"python": _resolve_python, "js": _resolve_js, "java": _resolve_java, "c": _resolve_c}[imp.kind]
            for target in resolver(imp, path, index):
                edges.add((path, target))
    return sorted(edges)


def transitive_importers(edges: list[tuple[str, str]], path: str) -> list[str]:
    """Everything that (directly or not) imports `path`: what may break when it changes."""
    importers: dict[str, set[str]] = defaultdict(set)
    for src, dst in edges:
        importers[dst].add(src)
    seen: set[str] = set()
    stack = [path]
    while stack:
        for parent in importers.get(stack.pop(), ()):
            if parent not in seen and parent != path:
                seen.add(parent)
                stack.append(parent)
    return sorted(seen)


@dataclass
class ProjectFacts:
    """Everything the dashboard shows, computed once per set of readable files."""
    stack: list[StackItem] = field(default_factory=list)
    entry_points: list[tuple[str, str]] = field(default_factory=list)
    env_vars: list[EnvVar] = field(default_factory=list)
