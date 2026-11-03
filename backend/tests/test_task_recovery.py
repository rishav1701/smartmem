import aiosqlite
import pytest

from workers import task_queue


@pytest.mark.asyncio
async def test_recovery_marks_only_interrupted_tasks_failed(tmp_path, monkeypatch):
    db_path = tmp_path / "tasks.sqlite3"
    monkeypatch.setattr(task_queue, "DB_PATH", db_path)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, message TEXT, updated_at TEXT)"
        )
        await db.executemany(
            "INSERT INTO tasks(id, status) VALUES (?, ?)",
            [("queued", "queued"), ("running", "running"), ("done", "done")],
        )
        await db.commit()

    changed = await task_queue.recover_interrupted_tasks()

    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("SELECT id, status, message FROM tasks ORDER BY id") as cur:
            states = {row[0]: (row[1], row[2]) for row in await cur.fetchall()}
    assert changed == 2
    assert states["queued"] == (
        "failed", "Interrupted by application restart; retry when ready"
    )
    assert states["running"][0] == "failed"
    assert states["done"] == ("done", None)
