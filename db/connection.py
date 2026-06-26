from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

load_dotenv(override=True)


def _database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        url = "postgresql+asyncpg://evotraders:evotraders@localhost:5432/evotraders"
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine: AsyncEngine = create_async_engine(
    _database_url(),
    pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
    max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "10")),
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    schema_path = Path(__file__).with_name("schema.sql")
    schema = schema_path.read_text(encoding="utf-8")
    statements = [stmt.strip() for stmt in schema.split(";") if stmt.strip()]
    async with engine.begin() as conn:
        for statement in statements:
            await conn.execute(text(statement))


async def close_db() -> None:
    await engine.dispose()
