import aiosqlite
import pytest

from database import db as database


@pytest.mark.asyncio
async def test_init_db_migrates_legacy_file_table(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    async with aiosqlite.connect(str(db_path)) as conn:
        await conn.execute(
            "CREATE TABLE files_metadata (id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, "
            "filename TEXT NOT NULL, extension TEXT, size_bytes INTEGER NOT NULL DEFAULT 0, "
            "created_at TEXT, modified_at TEXT, accessed_at TEXT, sha256 TEXT, "
            "ai_category TEXT, embedding_id INTEGER, scanned_at TEXT, is_deleted INTEGER)"
        )
        await conn.execute(
            "INSERT INTO files_metadata(path, filename, size_bytes) VALUES (?, ?, ?)",
            ("/preserve/notes.txt", "notes.txt", 123),
        )
        await conn.commit()

    await database.init_db()

    async with aiosqlite.connect(str(db_path)) as conn:
        async with conn.execute("PRAGMA table_info(files_metadata)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ) as cur:
            versions = [row[0] for row in await cur.fetchall()]
        async with conn.execute("SELECT path, filename, size_bytes FROM files_metadata") as cur:
            preserved = await cur.fetchone()
    assert "doc_text" in columns
    assert versions == [1, 2]
    assert tuple(preserved) == ("/preserve/notes.txt", "notes.txt", 123)
