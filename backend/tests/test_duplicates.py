import aiosqlite
import pytest

from services.duplicate import duplicate_finder


@pytest.mark.asyncio
async def test_duplicate_summary_uses_active_file_rows_not_stale_hash_index(tmp_path, monkeypatch):
    db_path = tmp_path / "duplicates.sqlite3"
    monkeypatch.setattr(duplicate_finder, "DB_PATH", db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "CREATE TABLE files_metadata (id INTEGER PRIMARY KEY, path TEXT, filename TEXT, "
            "size_bytes INTEGER, modified_at TEXT, accessed_at TEXT, sha256 TEXT, is_deleted INTEGER)"
        )
        await db.execute("CREATE TABLE hash_index (sha256 TEXT PRIMARY KEY, file_count INTEGER)")
        await db.executemany(
            "INSERT INTO files_metadata(path, filename, size_bytes, sha256, is_deleted) "
            "VALUES (?, ?, ?, ?, ?)",
            [("a", "a", 100, "same", 0), ("b", "b", 100, "same", 0),
             ("c", "c", 100, "same", 1), ("d", "d", 20, "unique", 0)],
        )
        await db.execute("INSERT INTO hash_index VALUES ('same', 900)")
        await db.commit()

    summary = await duplicate_finder.get_duplicate_summary()
    groups = await duplicate_finder.get_duplicates(min_size=50)

    assert summary == {"duplicate_groups": 1, "extra_files": 1, "wasted_bytes": 100}
    assert len(groups) == 1
    assert groups[0]["count"] == 2
    assert groups[0]["wasted_bytes"] == 100
