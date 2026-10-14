import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import settings_store
from app.api import conversations, dev, files, meta, projects, settings as settings_api
from app.config import settings
from app.db import SessionLocal, engine, ping_postgres
from app.ingestion import worker
from app.search.es_client import es, ping_elasticsearch

log = logging.getLogger("devdocs")
if not log.handlers:  # uvicorn only configures its own loggers; show our INFO lines ("LLM not called", ...)
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:  # saved settings win over .env; projects embedded with another model are flagged for re-index
        async with SessionLocal() as session:
            await settings_store.load_overrides(session)
            await settings_api.sync_reindex_flags(session)
            await session.commit()
    except Exception:
        log.exception("could not load saved settings; continuing with .env values")
    await worker.start()
    yield
    await worker.stop()
    await es.close()
    await engine.dispose()


app = FastAPI(title="DevDocs", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(projects.router)
app.include_router(files.router)
app.include_router(meta.router)
app.include_router(conversations.router)
app.include_router(settings_api.router)
app.include_router(dev.router)


async def _check(name: str, probe) -> str:
    try:
        await asyncio.wait_for(probe(), timeout=3)
        return "ok"
    except Exception as exc:  # health must never raise; report the failure instead
        log.warning("health check failed for %s: %r", name, exc)
        return "error"


@app.get("/health")
async def health() -> dict[str, str]:
    postgres, elasticsearch = await asyncio.gather(
        _check("postgres", ping_postgres),
        _check("elasticsearch", ping_elasticsearch),
    )
    return {"postgres": postgres, "elasticsearch": elasticsearch}
