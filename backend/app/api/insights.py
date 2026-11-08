"""What DevDocs knows about a project, shown without any model call: dashboard, keyword search, symbols,
starter questions and the import graph. Everything is computed from files the *signed-in person* may read
(`access.readable_files`), so a hidden file is not listed, counted, searched, drawn or suggested.

The static analysis (stack, env vars, symbols, edges) reads the stored files and is cached in memory per
set of readable (file, hash) pairs: it is recomputed when a file changes or when the person's access changes."""
import asyncio
import logging
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, analysis
from app.api.files import disk_path
from app.auth_deps import current_user, file_for, project_for
from app.db import get_session
from app.models import Chunk, File, User
from app.retrieval import pipeline
from app.retrieval.bm25 import bm25_search

router = APIRouter(prefix="/projects/{project_id}", tags=["insights"])
log = logging.getLogger("devdocs.insights")

MAX_ANALYZED_BYTES = 1_000_000  # a larger file is not read for analysis (it still counts as a file)
TEXT_SUFFIXES = (".pdf", ".docx")
CACHE_SIZE = 32


# ----- computed once per set of readable files -----
@dataclass
class Insights:
    facts: analysis.ProjectFacts
    symbols: list[tuple[analysis.Symbol, uuid.UUID]] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    largest: list[tuple[str, int]] = field(default_factory=list)
    readme_path: str | None = None
    readme_excerpt: str = ""


_cache: OrderedDict[tuple, Insights] = OrderedDict()


def _read_texts(project_id: uuid.UUID, rows: list[File]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows:
        if row.file_path.lower().endswith(TEXT_SUFFIXES) or row.file_size > MAX_ANALYZED_BYTES:
            continue
        try:
            out[row.file_path] = disk_path(project_id, row.file_path).read_text("utf-8", errors="replace")
        except OSError:
            continue
    return out


def _compute(project_id: uuid.UUID, rows: list[File]) -> Insights:
    texts = _read_texts(project_id, rows)
    by_path = {r.file_path: r for r in rows}
    manifests = {p: t for p, t in texts.items() if p.rsplit("/", 1)[-1].lower() in analysis.MANIFESTS}
    facts = analysis.ProjectFacts(
        stack=analysis.detect_stack(manifests),
        entry_points=analysis.find_entry_points(texts),
        env_vars=analysis.find_env_vars(texts),
    )
    symbols: list[tuple[analysis.Symbol, uuid.UUID]] = []
    for path, text in texts.items():
        for sym in analysis.find_symbols(path, text, by_path[path].language):
            symbols.append((sym, by_path[path].id))
    edges = analysis.build_graph({p: (t, by_path[p].language) for p, t in texts.items()})
    readme = next(
        (
            p for p in sorted(texts, key=lambda x: (x.count("/"), x))
            if p.rsplit("/", 1)[-1].lower() in ("readme.md", "readme.txt")
            and not any(part.startswith(".") for part in p.split("/")[:-1])  # never a tool cache such as .pytest_cache
        ),
        None,
    )
    largest = sorted(((r.file_path, r.file_size) for r in rows), key=lambda x: -x[1])[:5]
    return Insights(facts, symbols, edges, largest, readme, texts[readme][:1500] if readme else "")


async def _insights(session: AsyncSession, user: User, project_id: uuid.UUID) -> tuple[Insights, list[File]]:
    allowed = await access.readable_file_ids(session, user, project_id)
    rows = list((await session.scalars(
        select(File).where(File.project_id == project_id, File.id.in_(allowed), File.status != "failed")
        .order_by(File.file_path)
    )).all()) if allowed else []
    key = (project_id, tuple((str(r.id), r.file_hash) for r in rows))
    hit = _cache.get(key)
    if hit is None:
        hit = await asyncio.to_thread(_compute, project_id, rows)
        _cache[key] = hit
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    else:
        _cache.move_to_end(key)
    return hit, rows


def clear_cache() -> None:
    _cache.clear()


# ----- dashboard -----
class StackOut(BaseModel):
    name: str
    source: str


class EntryOut(BaseModel):
    path: str
    reason: str


class EnvOut(BaseModel):
    name: str
    path: str
    line: int
    declared: bool


class FileSize(BaseModel):
    path: str
    size: int


class Readme(BaseModel):
    path: str
    excerpt: str


class LanguageOut(BaseModel):
    language: str | None
    count: int


class DashboardOut(BaseModel):
    file_count: int
    chunk_count: int
    by_type: dict[str, int]
    languages: list[LanguageOut]
    stack: list[StackOut]
    entry_points: list[EntryOut]
    env_vars: list[EnvOut]
    largest: list[FileSize]
    readme: Readme | None


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    await project_for(session, user, project_id)
    info, rows = await _insights(session, user, project_id)
    ids = [r.id for r in rows]
    chunks = (await session.scalar(select(func.count()).select_from(Chunk).where(Chunk.file_id.in_(ids)))) if ids else 0
    langs: dict[str | None, int] = {}
    for r in rows:
        if r.language:
            langs[r.language] = langs.get(r.language, 0) + 1
    return DashboardOut(
        file_count=len(rows), chunk_count=chunks or 0,
        by_type={t: sum(1 for r in rows if r.file_type == t) for t in ("doc", "source", "other")},
        languages=[LanguageOut(language=k, count=v) for k, v in sorted(langs.items(), key=lambda kv: (-kv[1], kv[0] or ""))],
        stack=[StackOut(name=s.name, source=s.source) for s in info.facts.stack],
        entry_points=[EntryOut(path=p, reason=why) for p, why in info.facts.entry_points[:8]],
        env_vars=[EnvOut(name=v.name, path=v.path, line=v.line, declared=v.declared) for v in info.facts.env_vars],
        largest=[FileSize(path=p, size=s) for p, s in info.largest],
        readme=Readme(path=info.readme_path, excerpt=info.readme_excerpt) if info.readme_path else None,
    )


# ----- symbols -----
class SymbolOut(BaseModel):
    name: str
    kind: str
    path: str
    line: int
    file_id: uuid.UUID


@router.get("/symbols", response_model=list[SymbolOut])
async def symbols(
    project_id: uuid.UUID, q: str = Query("", max_length=200), limit: int = Query(100, ge=1, le=500),
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    await project_for(session, user, project_id)
    info, _ = await _insights(session, user, project_id)
    needle = q.strip().lower()
    found = [(s, fid) for s, fid in info.symbols if not needle or needle in s.name.lower() or needle in s.path.lower()]
    found.sort(key=lambda sf: (needle != sf[0].name.lower(), sf[0].name.lower(), sf[0].path, sf[0].line))
    return [SymbolOut(name=s.name, kind=s.kind, path=s.path, line=s.line, file_id=fid) for s, fid in found[:limit]]


# ----- starter questions -----
class StartersOut(BaseModel):
    questions: list[str]


@router.get("/starters", response_model=StartersOut)
async def starters(
    project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Questions built from this project's own symbols, env vars and files by templates: nothing is
    invented, nothing about a file the person cannot read, and no model call."""
    await project_for(session, user, project_id)
    info, rows = await _insights(session, user, project_id)
    out: list[str] = []
    if info.readme_path:
        out.append(f"Summarise {info.readme_path}")
    if info.facts.entry_points:
        out.append(f"How does the program start? Look at {info.facts.entry_points[0][0]}")
    big = sorted(
        (s for s, _ in info.symbols if s.kind in ("function", "class", "method") and not s.name.startswith("_")),
        key=lambda s: (s.name.count("."), -len(s.name)),
    )
    seen_files: set[str] = set()
    for sym in big:
        if sym.path in seen_files:
            continue
        seen_files.add(sym.path)
        out.append(f"What does `{sym.name}` in {sym.path} do?")
        if len(seen_files) >= 2:
            break
    if info.facts.env_vars:
        out.append(f"How is `{info.facts.env_vars[0].name}` used?")
    if not out and rows:
        out.append(f"What is in {rows[0].file_path}?")
    return StartersOut(questions=out[:4])


# ----- search -----
class SearchHit(BaseModel):
    file_id: uuid.UUID
    path: str
    language: str | None
    file_type: str
    line_start: int | None
    line_end: int | None
    page_number: int | None
    section: str | None
    snippet: str
    score: float


class SearchOut(BaseModel):
    query: str
    hits: list[SearchHit]
    searched_files: int


@router.get("/search", response_model=SearchOut)
async def search(
    project_id: uuid.UUID, q: str = Query(min_length=2, max_length=300),
    language: str | None = Query(None, max_length=30), file_type: str | None = Query(None, pattern="^(doc|source|other)$"),
    folder: str | None = Query(None, max_length=500), limit: int = Query(20, ge=1, le=50),
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Keyword (BM25) search over the content, path and function / heading names of the files the person may
    read. No embedding call, so it is instant and uses no quota."""
    await project_for(session, user, project_id)
    allowed = await access.readable_file_ids(session, user, project_id, ready_only=True)
    query = select(File).where(File.project_id == project_id, File.id.in_(allowed))
    if language:
        query = query.where(File.language == language)
    if file_type:
        query = query.where(File.file_type == file_type)
    if folder:
        prefix = folder.strip().strip("/")
        if prefix:
            query = query.where(File.file_path.like(prefix.replace("%", r"\%").replace("_", r"\_") + "/%", escape="\\"))
    files = {f.id: f for f in (await session.scalars(query)).all()} if allowed else {}
    if not files:
        return SearchOut(query=q, hits=[], searched_files=0)
    fingerprint = await pipeline.resolve_fingerprint(session, project_id)
    try:
        hits = await bm25_search(fingerprint, project_id, list(files), q, limit)
    except Exception as exc:
        log.warning("search failed for project %s: %r", project_id, exc)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Search is unavailable right now.") from exc
    chunks = {
        str(c.id): c for c in (await session.scalars(
            select(Chunk).where(Chunk.id.in_([uuid.UUID(h.chunk_id) for h in hits]), Chunk.file_id.in_(list(files)))
        )).all()
    } if hits else {}
    out = []
    for h in hits:
        chunk, file = chunks.get(h.chunk_id), files.get(uuid.UUID(h.file_id))
        if chunk is None or file is None:  # a stray ES doc is never shown
            continue
        out.append(SearchHit(
            file_id=file.id, path=file.file_path, language=file.language, file_type=file.file_type,
            line_start=chunk.line_start, line_end=chunk.line_end, page_number=chunk.page_number,
            section=chunk.section, snippet=chunk.content[:400], score=h.score,
        ))
    return SearchOut(query=q, hits=out, searched_files=len(files))


# ----- import graph -----
class GraphNode(BaseModel):
    id: uuid.UUID
    path: str
    language: str | None
    imports: int
    imported_by: int


class GraphEdge(BaseModel):
    source: uuid.UUID
    target: uuid.UUID


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    files_without_links: int
    note: str = "Static imports only: dynamic loading, dependency injection and string-based routing are not drawn."


@router.get("/graph", response_model=GraphOut)
async def graph(
    project_id: uuid.UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session),
):
    """Files that import each other. A file the person cannot read is not a node, and an edge exists only when both
    ends are readable, because edges are resolved among readable files only."""
    await project_for(session, user, project_id)
    info, rows = await _insights(session, user, project_id)
    ids = {r.file_path: r.id for r in rows}
    out_count: dict[str, int] = {}
    in_count: dict[str, int] = {}
    for src, dst in info.edges:
        out_count[src] = out_count.get(src, 0) + 1
        in_count[dst] = in_count.get(dst, 0) + 1
    linked = set(out_count) | set(in_count)
    language = {r.file_path: r.language for r in rows}
    nodes = [
        GraphNode(id=ids[p], path=p, language=language[p], imports=out_count.get(p, 0), imported_by=in_count.get(p, 0))
        for p in sorted(linked)
    ]
    code = sum(1 for r in rows if r.language in analysis.GRAPH_LANGUAGES)
    return GraphOut(
        nodes=nodes, edges=[GraphEdge(source=ids[s], target=ids[d]) for s, d in info.edges],
        files_without_links=max(code - len(linked), 0),
    )


class ImpactOut(BaseModel):
    path: str
    imports: list[str]
    imported_by: list[str]
    affected: list[str]  # direct and indirect importers: what may break when this file changes


@router.get("/graph/{file_id}", response_model=ImpactOut)
async def impact(
    project_id: uuid.UUID, file_id: uuid.UUID, user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await project_for(session, user, project_id)
    row = await file_for(session, user, file_id)
    if row.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    info, _ = await _insights(session, user, project_id)
    return ImpactOut(
        path=row.file_path,
        imports=sorted({d for s, d in info.edges if s == row.file_path}),
        imported_by=sorted({s for s, d in info.edges if d == row.file_path}),
        affected=analysis.transitive_importers(info.edges, row.file_path),
    )
