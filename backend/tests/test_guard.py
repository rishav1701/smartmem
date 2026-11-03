import pytest
import aiosqlite
from fastapi import HTTPException

from security import guard


def test_resolve_safe_rejects_parent_traversal(tmp_path):
    with pytest.raises(HTTPException) as exc:
        guard.resolve_safe(str(tmp_path / ".." / "outside"))
    assert exc.value.status_code == 400


def test_resolve_safe_rejects_symlinked_ancestor(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("private", encoding="utf-8")
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(HTTPException) as exc:
        guard.resolve_safe(str(link / "secret.txt"))
    assert exc.value.status_code == 400


def test_safe_delete_moves_file_without_overwriting(tmp_path, monkeypatch):
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    source = tmp_path / "document.txt"
    source.write_text("preserve me", encoding="utf-8")
    monkeypatch.setattr(guard, "RECYCLE_BIN", recycle)

    destination = guard.safe_delete(source)

    assert not source.exists()
    assert destination.parent == recycle
    assert destination.read_text(encoding="utf-8") == "preserve me"


def test_safe_delete_rejects_symlink(tmp_path, monkeypatch):
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(guard, "RECYCLE_BIN", recycle)

    with pytest.raises(HTTPException):
        guard.safe_delete(link)
    assert target.read_text(encoding="utf-8") == "keep"


def test_safe_delete_refuses_directories(tmp_path, monkeypatch):
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "keep.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(guard, "RECYCLE_BIN", recycle)

    with pytest.raises(HTTPException):
        guard.safe_delete(folder)
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.asyncio
async def test_scope_check_supports_default_sqlite_row_factory(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("CREATE TABLE directories(path TEXT)")
        await db.execute("INSERT INTO directories(path) VALUES (?)", (str(allowed),))
        await guard.assert_under_allowed(allowed / "nested" / "file.txt", db)
        with pytest.raises(HTTPException) as exc:
            await guard.assert_under_allowed(tmp_path / "other.txt", db)
    assert exc.value.status_code == 403

