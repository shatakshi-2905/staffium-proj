"""Connection pool and the two ways the app talks to Postgres.

* user_tx(user)  - every request made on behalf of a signed-in user. Inside the transaction the
                   connection switches to the restricted `app_user` role and records the caller's
                   id, so Row-Level Security policies decide what the query can see or change.
* system_tx()    - internal work the user doesn't perform directly (resume processing, the email
                   worker, auth lookups). Runs as the connection's own role, bypassing RLS, and is
                   only reachable from server-side code paths that have already authorised the action.
"""
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

import asyncpg

from .config import get_settings

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def open_pool() -> None:
    global _pool
    s = get_settings()
    _pool = await asyncpg.create_pool(
        s.database_url,
        min_size=s.db_pool_min,
        max_size=s.db_pool_max,
        # Supabase's pooler (PgBouncer/Supavisor in transaction mode) can't keep prepared statements.
        statement_cache_size=0,
        command_timeout=30,
        init=_init_connection,
        server_settings={"search_path": "public, extensions", "application_name": "shortlist-api"},
    )


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database pool is not open")
    return _pool


@asynccontextmanager
async def user_tx(user_id: UUID | str) -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            # set_config(..., true) is transaction-local, so nothing leaks to the next request that
            # reuses this pooled connection.
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true), set_config('role', 'app_user', true)",
                json.dumps({"sub": str(user_id), "role": "authenticated"}),
            )
            yield conn


@asynccontextmanager
async def system_tx() -> AsyncIterator[asyncpg.Connection]:
    async with pool().acquire() as conn:
        async with conn.transaction():
            yield conn


def vector_literal(values) -> str:
    """pgvector text format. Values are floats we produced ourselves, never user input."""
    return "[" + ",".join(f"{float(v):.6f}" for v in values) + "]"
