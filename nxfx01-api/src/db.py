"""Async Postgres connection pool for NXFX01 pipeline."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

_pool: asyncpg.Pool | None = None


def _dsn() -> str:
    dsn = os.getenv("NXFX01_DATABASE_URL")
    if not dsn:
        raise RuntimeError("NXFX01_DATABASE_URL environment variable is required")
    return dsn


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            _dsn(),
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connection():
    """Yield a single connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    pool = await get_pool()
    return await pool.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    pool = await get_pool()
    return await pool.execute(query, *args)


# ---------------------------------------------------------------------------
# Config helpers (nxfx01_config table)
# ---------------------------------------------------------------------------

async def get_config(key: str) -> Any:
    """Read a single value from nxfx01_config. Returns the JSON-decoded value."""
    import json
    raw = await fetchval("SELECT value FROM nxfx01_config WHERE key = $1", key)
    if raw is None:
        return None
    return json.loads(raw) if isinstance(raw, str) else raw


async def set_config(key: str, value: Any) -> None:
    """Upsert a value into nxfx01_config."""
    import json
    json_val = json.dumps(value)
    await execute(
        """
        INSERT INTO nxfx01_config (key, value)
        VALUES ($1, $2::jsonb)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        key,
        json_val,
    )


async def is_shadow_mode() -> bool:
    mode = await get_config("mode")
    return mode != "live"
