from concurrent.futures import ThreadPoolExecutor

import aiosqlite
import pytest

from services.organizer import ai_organizer


def test_concurrent_destination_reservations_never_overwrite(tmp_path):
    source_a = tmp_path / "source-a" / "report.txt"
    source_b = tmp_path / "source-b" / "report.txt"
    destination = tmp_path / "organized"
    source_a.parent.mkdir()
    source_b.parent.mkdir()
    destination.mkdir()
    source_a.write_text("first document", encoding="utf-8")
    source_b.write_text("second document", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(
            lambda _: ai_organizer._reserve_destination(destination, "report.txt"),
            range(2),
        ))

    assert reservations[0][0] != reservations[1][0]
    ai_organizer._move_into_reservation(source_a, *reservations[0])
    ai_organizer._move_into_reservation(source_b, *reservations[1])

    assert not source_a.exists()
    assert not source_b.exists()
    assert {path.read_text(encoding="utf-8") for path in destination.iterdir()} == {
        "first document", "second document"
    }


def test_destination_reservation_preserves_existing_file(tmp_path):
    existing = tmp_path / "report.txt"
    existing.write_text("keep this", encoding="utf-8")

    reserved, token = ai_organizer._reserve_destination(tmp_path, "report.txt")
    source = tmp_path / "new-report.txt"
    source.write_text("new content", encoding="utf-8")
    ai_organizer._move_into_reservation(source, reserved, token)

    assert existing.read_text(encoding="utf-8") == "keep this"
    assert reserved.read_text(encoding="utf-8") == "new content"


@pytest.mark.asyncio
async def test_live_organizer_failure_is_reported_and_remains_retryable(tmp_path, monkeypatch):
    root = tmp_path / "selected"
    root.mkdir()
    source = root / "report.bin"
    source.write_bytes(b"document")
    db_path = tmp_path / "organizer.sqlite3"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE directories (path TEXT)")
        await db.execute(
            "CREATE TABLE files_metadata (id INTEGER PRIMARY KEY, path TEXT, "
            "filename TEXT, ai_category TEXT, is_deleted INTEGER)"
        )
        await db.execute("INSERT INTO directories(path) VALUES (?)", (str(root),))
        await db.execute(
            "INSERT INTO files_metadata(path, filename, is_deleted) VALUES (?, ?, 0)",
            (str(source), source.name),
        )
        await db.commit()

    updates = []

    async def record_update(*args, **kwargs):
        updates.append(args)

    async def choose_category(*args, **kwargs):
        return "Others"

    async def fail_move(*args, **kwargs):
        return False

    monkeypatch.setattr(ai_organizer, "DB_PATH", db_path)
    monkeypatch.setattr(ai_organizer, "update_task", record_update)
    monkeypatch.setattr(ai_organizer, "is_cancelled", lambda _task_id: False)
    monkeypatch.setattr(ai_organizer, "_ask_ollama", choose_category)
    monkeypatch.setattr(ai_organizer, "_move_file_async", fail_move)

    await ai_organizer.categorize_files("task-id", False, str(root))

    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("SELECT ai_category FROM files_metadata") as cursor:
            category = (await cursor.fetchone())[0]
    assert category is None
    assert updates[-1][1] == "failed"
    assert "1 live move(s) failed" in updates[-1][3]
