from __future__ import annotations

from datetime import datetime
from os import getenv
from typing import Optional

import asyncpg
import pytz
from asyncpg import Pool


_pool: Optional[Pool] = None


def _get_dsn() -> str:
    db_name = getenv("DB_NAME", "postgres")
    db_user = getenv("DB_USER", "postgres")
    db_pass = getenv("DB_PASS", "")
    db_host = getenv("DB_HOST", "127.0.0.1")
    db_port = getenv("DB_PORT", "5432")
    return f"postgres://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"


async def init_pool() -> Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=_get_dsn(),
            min_size=1,
            max_size=10,
            command_timeout=60,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


class Database:
    def __init__(self):
        self.pool: Optional[Pool] = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None

    @property
    def now_msk(self) -> datetime:
        return datetime.now(pytz.timezone("Europe/Moscow")).replace(tzinfo=None)

    async def connect(self):
        self.pool = await init_pool()
        return self

    async def disconnect(self):
        return None

    async def _ensure_pool(self) -> Pool:
        if not self.pool:
            await self.connect()
        return self.pool

    async def execute(self, query, *args):
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query, *args):
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query, *args):
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query, *args):
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await conn.fetchval(query, *args)
