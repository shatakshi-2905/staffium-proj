"""Apply database/migrations/*.sql in order, once each.

Usage:
    DATABASE_URL=postgresql://... python database/migrate.py            # Supabase / production
    DATABASE_URL=postgresql://... python database/migrate.py --local    # plain Postgres: adds an auth.users stub
    DATABASE_URL=postgresql://... python database/migrate.py --status   # list applied migrations

Each file runs in its own transaction and is recorded in app.schema_migrations with its checksum,
so re-running is safe and an edited, already-applied file is reported instead of silently skipped.
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))


async def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    conn = await asyncpg.connect(url, statement_cache_size=0)
    try:
        if "--local" in sys.argv:
            await conn.execute((ROOT / "dev" / "auth_stub.sql").read_text())
        await conn.execute("""
            CREATE SCHEMA IF NOT EXISTS app;
            CREATE TABLE IF NOT EXISTS app.schema_migrations (
                filename   TEXT PRIMARY KEY,
                checksum   CHAR(64) NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
        applied = {r["filename"]: r["checksum"] for r in await conn.fetch("SELECT filename, checksum FROM app.schema_migrations")}

        if "--status" in sys.argv:
            for f in MIGRATIONS:
                print(("applied  " if f.name in applied else "pending  ") + f.name)
            return 0

        for f in MIGRATIONS:
            sql = f.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if f.name in applied:
                if applied[f.name] != digest:
                    print(f"WARNING {f.name} changed after it was applied; write a new migration instead")
                continue
            print(f"applying {f.name} ...", end=" ", flush=True)
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO app.schema_migrations (filename, checksum) VALUES ($1, $2)", f.name, digest)
            print("ok")
        print("database is up to date")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
