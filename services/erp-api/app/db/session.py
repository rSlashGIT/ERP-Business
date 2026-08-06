"""Async engine + session factory."""
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://erp:erp@postgres:5432/erp"
)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    pool_pre_ping=True,          # survive Postgres restarts / idle reaping
    pool_recycle=1800,
    echo=os.getenv("SQL_ECHO", "").lower() == "true",
)
_session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def async_session_factory():
    async with _session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_session():
    """FastAPI dependency."""
    async with _session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
