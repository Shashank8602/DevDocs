import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import dev, files, meta, projects
from app.config import settings
from app.db import engine, ping_postgres
from app.ingestion import worker
from app.search.es_client import es, ping_elasticsearch

log = logging.getLogger("devdocs")


@asynccontextmanager
async def lifespan(_: FastAPI):
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
