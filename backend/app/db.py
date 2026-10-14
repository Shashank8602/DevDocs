from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """For code that opens short sessions of its own (a streamed chat answer must not hold one connection
    for the whole stream). Tests override this dependency, as they do `get_session`."""
    return SessionLocal


async def ping_postgres() -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
