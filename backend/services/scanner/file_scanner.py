"""
services/scanner/file_scanner.py
----------------------------------
File system scanner + SHA-256 hasher.

Fixes vs previous version:
  - Single DB connection for entire hash run (was: one per file → SQLite chaos)
  - Concurrent hashing via asyncio.to_thread + Semaphore(8)
  - hash_index rebuilt correctly from scratch each run (no stale counts)
  - Re-scanned files whose modified_at changed get sha256 reset to NULL
  - Progress updates every 10 files (was: every 50)
  - Proper error handling per file without aborting the whole task
"""

import os
import asyncio
import hashlib
import logging
import pathlib
import stat
import uuid
from datetime import datetime

import aiosqlite
from database.db import DB_PATH
from workers.task_queue import update_task
from security.guard import validate_directory, assert_under_allowed

logger = logging.getLogger(__name__)

CHUNK       = 65536   # 64 KB read chunks
HASH_SEM    = asyncio.Semaphore(8)   # 8 concurrent hash threads
COMMIT_EVERY = 100    # commit to DB every N files
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _is_link_or_reparse(path: pathlib.Path, errors: list | None = None) -> bool:
    try:
        st = path.lstat()
        is_junction = hasattr(path, "is_junction") and path.is_junction()
        return (path.is_symlink() or is_junction or
                bool(getattr(st, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT))
    except FileNotFoundError:
        return True
    except OSError as exc:
        if errors is not None:
            errors.append(exc)
        return True


# ── Scanner ───────────────────────────────────────────────────────────────────

def _stat_to_dict(p: pathlib.Path) -> dict | None:
    """Extract file metadata safely. Returns None on permission error."""
    try:
        if _is_link_or_reparse(p):
            return None
        st = p.lstat()
        return {
            "path":        str(p),
            "filename":    p.name,
            "extension":   p.suffix.lower(),
            "size_bytes":  st.st_size,
            "created_at":  datetime.fromtimestamp(st.st_ctime).isoformat(),
            "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
            "accessed_at": datetime.fromtimestamp(st.st_atime).isoformat(),
        }
    except (PermissionError, OSError):
        return None


def _iter_files(root: pathlib.Path, errors: list | None = None):
    """Walk without entering symlinked directories or Windows junctions."""
    def record_error(exc):
        if errors is not None:
            errors.append(exc)

    for current, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=record_error
    ):
        current_path = pathlib.Path(current)
        dirnames[:] = [name for name in dirnames
                       if not _is_link_or_reparse(current_path / name, errors)]
        for filename in filenames:
            candidate = current_path / filename
            if _is_link_or_reparse(candidate, errors):
                continue
            yield candidate


async def scan_directory(task_id: str, root: str):
    """
    Walk root recursively, upsert metadata into SQLite.
    If a file's modified_at changed, reset its sha256 so it gets re-hashed.
    """
    root_path = validate_directory(root)
    async with aiosqlite.connect(str(DB_PATH)) as scope_db:
        await assert_under_allowed(root_path, scope_db)
    if not root_path.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    logger.info("Scanning: %s", root)

    scan_id = str(uuid.uuid4())
    # Pre-count for progress bar. Do not reconcile missing files if the walk
    # reports permission/I/O errors, because unseen files may still exist.
    walk_errors = []
    total = sum(1 for _ in _iter_files(root_path, walk_errors))
    if walk_errors:
        logger.warning("Directory scan has inaccessible paths under %s", root_path)

    processed = 0
    skipped = 0
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")

        for fp in _iter_files(root_path, walk_errors):
                meta = _stat_to_dict(fp)
                if not meta:
                    processed += 1
                    skipped += 1
                    continue
                meta["scan_id"] = scan_id

                # Invalidate derived content and indexes when source mtime changes.
                await db.execute(
                    """INSERT INTO files_metadata
                           (path, filename, extension, size_bytes,
                            created_at, modified_at, accessed_at, scan_id, scanned_at)
                       VALUES (:path, :filename, :extension, :size_bytes,
                               :created_at, :modified_at, :accessed_at, :scan_id, datetime('now'))
                       ON CONFLICT(path) DO UPDATE SET
                           size_bytes   = excluded.size_bytes,
                           accessed_at  = excluded.accessed_at,
                           scanned_at   = datetime('now'),
                           -- reset sha256 if file was modified
                           sha256       = CASE WHEN modified_at != excluded.modified_at
                                               THEN NULL ELSE sha256 END,
                           embedding_id = CASE WHEN modified_at != excluded.modified_at
                                               THEN NULL ELSE embedding_id END,
                           ai_category  = CASE WHEN modified_at != excluded.modified_at
                                               THEN NULL ELSE ai_category END,
                           doc_text     = CASE WHEN modified_at != excluded.modified_at
                                               THEN NULL ELSE doc_text END,
                           scan_id      = excluded.scan_id,
                           is_deleted   = 0,
                           modified_at  = excluded.modified_at""",
                    meta,
                )
                processed += 1

                if processed % COMMIT_EVERY == 0:
                    await db.commit()
                    await update_task(
                        task_id, "running",
                        min(processed / max(total, 1), 0.99),
                        f"Scanned {processed}/{total} files"
                    )
                    await asyncio.sleep(0)

        await db.commit()
        if not walk_errors and not skipped:
            prefix = str(root_path) + os.sep
            await db.execute(
                "UPDATE files_metadata SET is_deleted=1, embedding_id=NULL, sha256=NULL "
                "WHERE (path=? OR substr(path, 1, ?) = ?) AND COALESCE(scan_id, '') != ?",
                (str(root_path), len(prefix), prefix, scan_id),
            )
        await db.execute("UPDATE directories SET last_scanned=datetime('now') WHERE path=?",
                         (str(root_path),))
        await db.commit()

    message = f"Scanned {processed} files"
    if walk_errors or skipped:
        message += "; incomplete scan, missing files were not reconciled"
    await update_task(task_id, "done", 1.0, message)
    logger.info("Scan complete: %d files in %s (incomplete=%s)", processed, root, bool(walk_errors or skipped))


# ── Hasher ────────────────────────────────────────────────────────────────────

def _compute_sha256(file_path: str) -> tuple[str, str | None]:
    """
    Blocking SHA-256 computation.
    Returns (file_path, sha256_hex_or_None).
    """
    h = hashlib.sha256()
    try:
        source = pathlib.Path(file_path)
        if _is_link_or_reparse(source):
            return file_path, None
        before = source.lstat()
        fd = os.open(file_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (
            opened.st_dev, opened.st_ino
        ):
            os.close(fd)
            return file_path, None
        with os.fdopen(fd, "rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
        return file_path, h.hexdigest()
    except (PermissionError, FileNotFoundError, OSError) as exc:
        logger.debug("Cannot hash %s: %s", file_path, exc)
        return file_path, None


async def _hash_one(path: str) -> tuple[str, str | None]:
    """Run SHA-256 in thread pool with concurrency limit."""
    async with HASH_SEM:
        return await asyncio.to_thread(_compute_sha256, path)


async def hash_all_files(task_id: str):
    """
    Compute SHA-256 for all files that have sha256=NULL.
    Uses concurrent thread-pool hashing and a single DB session per batch.
    Rebuilds hash_index from scratch to avoid stale counts.
    """
    # Fetch all un-hashed file records
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, path FROM files_metadata WHERE sha256 IS NULL AND is_deleted=0"
        ) as cur:
            rows = await cur.fetchall()

    total = len(rows)
    if total == 0:
        await update_task(task_id, "done", 1.0, "All files already hashed")
        logger.info("All files already hashed")
        return

    logger.info("Hashing %d files ...", total)
    await update_task(task_id, "running", 0.0, f"Starting — {total} files to hash")

    BATCH_SIZE = 50    # hash + write N files at a time
    done_count = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = rows[batch_start : batch_start + BATCH_SIZE]

        # Hash all files in batch concurrently
        results = await asyncio.gather(*[_hash_one(r["path"]) for r in batch])

        # Write results in a single DB transaction
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            for row, (path, sha) in zip(batch, results):
                if sha:
                    await db.execute(
                        "UPDATE files_metadata SET sha256=? WHERE id=?",
                        (sha, row["id"])
                    )
                done_count += 1

            await db.commit()

        progress = done_count / total
        await update_task(
            task_id, "running", progress,
            f"Hashed {done_count}/{total} files"
        )
        await asyncio.sleep(0)   # yield to event loop between batches

    # ── Rebuild hash_index from scratch ──────────────────────────────────────
    # Do NOT use incremental +1 — always recompute to stay accurate
    await update_task(task_id, "running", 0.98, "Rebuilding duplicate index...")
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("DELETE FROM hash_index")
        await db.execute(
            """INSERT INTO hash_index (sha256, file_count)
               SELECT sha256, COUNT(*)
               FROM files_metadata
               WHERE sha256 IS NOT NULL AND is_deleted=0
               GROUP BY sha256"""
        )
        await db.commit()

    logger.info("Hashing complete: %d files processed", done_count)
