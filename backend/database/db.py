"""
database/db.py - SQLite schema & async connection helpers
"""

import aiosqlite
import logging

logger = logging.getLogger(__name__)

from config import DB_PATH

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS directories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT    NOT NULL UNIQUE,
    alias        TEXT,
    added_at     TEXT    DEFAULT (datetime('now')),
    last_scanned TEXT
);

CREATE TABLE IF NOT EXISTS files_metadata (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT    NOT NULL UNIQUE,
    filename     TEXT    NOT NULL,
    extension    TEXT,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT,
    modified_at  TEXT,
    accessed_at  TEXT,
    sha256       TEXT,
    ai_category  TEXT,
    embedding_id INTEGER,
    doc_text     TEXT,
    scan_id      TEXT,
    scanned_at   TEXT    DEFAULT (datetime('now')),
    is_deleted   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hash_index (
    sha256     TEXT    PRIMARY KEY,
    file_count INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    task_type  TEXT NOT NULL,
    status     TEXT DEFAULT 'queued',
    progress   REAL DEFAULT 0.0,
    message    TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    payload    TEXT
);

CREATE TABLE IF NOT EXISTS organizer_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    new_path      TEXT NOT NULL,
    moved_at      TEXT DEFAULT (datetime('now')),
    undone        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_files_sha    ON files_metadata(sha256);
CREATE INDEX IF NOT EXISTS idx_files_size   ON files_metadata(size_bytes);
CREATE INDEX IF NOT EXISTS idx_files_ext    ON files_metadata(extension);
CREATE INDEX IF NOT EXISTS idx_files_cat    ON files_metadata(ai_category);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

async def _apply_migrations(db: aiosqlite.Connection) -> None:
    """Apply ordered, idempotent schema upgrades to legacy SmartMem databases."""
    async with db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations") as cur:
        row = await cur.fetchone()
    version = int(row[0]) if row else 0

    if version < 1:
        async with db.execute("PRAGMA table_info(files_metadata)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "doc_text" not in columns:
            await db.execute("ALTER TABLE files_metadata ADD COLUMN doc_text TEXT")
        await db.execute("INSERT INTO schema_migrations(version) VALUES (1)")
        version = 1
        logger.info("Applied database migration %d", version)

    if version < 2:
        async with db.execute("PRAGMA table_info(files_metadata)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "scan_id" not in columns:
            await db.execute("ALTER TABLE files_metadata ADD COLUMN scan_id TEXT")
        await db.execute("INSERT INTO schema_migrations(version) VALUES (2)")
        logger.info("Applied database migration 2")

    await db.commit()


async def get_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(SCHEMA_SQL)
        await _apply_migrations(db)
    logger.info("✓ Database ready at %s", DB_PATH)
