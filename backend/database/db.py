"""
database/db.py - SQLite schema & async connection helpers
"""

import aiosqlite
import sqlite3
import pathlib
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

CREATE INDEX IF NOT EXISTS idx_files_sha    ON files_metadata(sha256);
CREATE INDEX IF NOT EXISTS idx_files_size   ON files_metadata(size_bytes);
CREATE INDEX IF NOT EXISTS idx_files_ext    ON files_metadata(extension);
CREATE INDEX IF NOT EXISTS idx_files_cat    ON files_metadata(ai_category);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""

# Migration: add doc_text column if it doesn't exist (for existing DBs)
MIGRATION_SQL = """
ALTER TABLE files_metadata ADD COLUMN doc_text TEXT;
"""


async def get_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(SCHEMA_SQL)
        # Run migration for existing DBs - only catch specific errors
        try:
            await db.execute(MIGRATION_SQL)
            await db.commit()
            logger.info("✓ Migration: added doc_text column")
        except sqlite3.OperationalError as exc:
            # Column likely already exists — this is expected
            if "duplicate column" in str(exc).lower():
                logger.info("✓ Column doc_text already exists (expected)")
            else:
                # Log unexpected errors
                logger.error("❌ Migration error: %s", exc)
                logger.warning("⚠ Database schema may be inconsistent")
        except Exception as exc:
            # Any other error should be logged clearly
            logger.error("❌ Unexpected migration error: %s", exc)
            raise
    logger.info("✓ Database ready at %s", DB_PATH)
