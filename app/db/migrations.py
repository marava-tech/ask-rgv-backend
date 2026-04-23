"""
Auto-migration runner — called at application startup.

Finds all *.sql files in the migrations/ directory (baked into the Docker image),
checks which have been applied via the schema_migrations tracking table, and runs
any that haven't.

Edge-case: if schema_migrations is empty but the DB already has tables (i.e. the DB
was set up before this tracking table existed), all present migration files are
recorded as already-applied without re-running them — so non-idempotent ALTER/CREATE
INDEX migrations won't be executed twice.
"""
import logging
from pathlib import Path

import asyncpg

_log = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

_CREATE_TRACKING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT now()
)
"""


async def run_pending(pool: asyncpg.Pool) -> int:
    """Run all unapplied migrations in order. Returns count of newly applied migrations."""
    await pool.execute(_CREATE_TRACKING)

    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    if not files:
        _log.warning("[migrations] no .sql files found in %s", _MIGRATIONS_DIR)
        return 0

    # If tracking table is empty but the DB has tables, this DB was initialised
    # before migration tracking was introduced — record all files as already applied
    # so we don't try to re-run non-idempotent DDL (CREATE UNIQUE INDEX etc.).
    recorded_count = await pool.fetchval("SELECT COUNT(*) FROM schema_migrations")
    if recorded_count == 0:
        db_initialised = await pool.fetchval(
            "SELECT EXISTS("
            "  SELECT 1 FROM information_schema.tables"
            "  WHERE table_schema = 'public' AND table_name = 'users'"
            ")"
        )
        if db_initialised:
            _log.info(
                "[migrations] existing DB detected — seeding %d migrations as already applied",
                len(files),
            )
            async with pool.acquire() as conn:
                await conn.executemany(
                    "INSERT INTO schema_migrations (filename) VALUES ($1) ON CONFLICT DO NOTHING",
                    [(f.name,) for f in files],
                )
            return 0

    applied = 0
    for path in files:
        fname = path.name
        already = await pool.fetchval(
            "SELECT 1 FROM schema_migrations WHERE filename = $1", fname
        )
        if already:
            _log.debug("[migrations] skip %s", fname)
            continue

        _log.info("[migrations] applying %s …", fname)
        sql = path.read_text()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)", fname
                )
        applied += 1
        _log.info("[migrations] applied  %s", fname)

    if applied:
        _log.info("[migrations] %d migration(s) applied", applied)
    else:
        _log.info("[migrations] schema up to date")

    return applied
