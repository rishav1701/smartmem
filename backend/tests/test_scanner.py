import pytest
import aiosqlite

from database import db as database
from services.scanner import file_scanner


def test_scanner_skips_symlinked_files_and_directories(tmp_path):
    root = tmp_path / "selected"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "local.txt").write_text("local", encoding="utf-8")
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    try:
        (root / "linked-file.txt").symlink_to(outside / "secret.txt")
        (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    found = set(file_scanner._iter_files(root))

    assert found == {root / "local.txt"}
    assert file_scanner._stat_to_dict(root / "linked-file.txt") is None


def test_hash_refuses_symlink(tmp_path):
    target = tmp_path / "original.txt"
    target.write_text("protected", encoding="utf-8")
    link = tmp_path / "alias.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    _, digest = file_scanner._compute_sha256(str(link))

    assert digest is None
    assert target.read_text(encoding="utf-8") == "protected"


@pytest.mark.asyncio
async def test_complete_scan_reconciles_missing_metadata_only_under_root(tmp_path, monkeypatch):
    root = tmp_path / "selected"
    root.mkdir()
    present = root / "present.txt"
    present.write_text("hello", encoding="utf-8")
    db_path = tmp_path / "scan.sqlite3"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(file_scanner, "DB_PATH", db_path)

    async def no_task_update(*args, **kwargs):
        return None

    monkeypatch.setattr(file_scanner, "update_task", no_task_update)
    await database.init_db()
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("INSERT INTO directories(path, alias) VALUES (?, ?)",
                         (str(root), "selected"))
        await db.execute(
            "INSERT INTO files_metadata(path, filename, size_bytes) VALUES (?, ?, ?)",
            (str(root / "removed.txt"), "removed.txt", 10),
        )
        await db.commit()

    await file_scanner.scan_directory("task-id", str(root))

    async with aiosqlite.connect(str(db_path)) as db:
        async with db.execute("SELECT path, is_deleted FROM files_metadata ORDER BY path") as cur:
            rows = await cur.fetchall()
    assert [(row[0], row[1]) for row in rows] == [
        (str(root / "present.txt"), 0), (str(root / "removed.txt"), 1)
    ]
