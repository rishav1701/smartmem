"""
services/duplicate/duplicate_finder.py – SHA-256 based duplicate detection
"""

import logging
import aiosqlite
from database.db import DB_PATH

logger = logging.getLogger(__name__)


async def get_duplicates(min_size: int = 1) -> list[dict]:
    """
    Return groups of duplicate files (same SHA-256, size > min_size bytes).
    Files with NULL sha256 are excluded (not yet hashed).
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        # Find hashes that appear more than once
        async with db.execute(
            """SELECT sha256, COUNT(*) as cnt, SUM(size_bytes) as total_size
               FROM files_metadata
               WHERE sha256 IS NOT NULL
                 AND is_deleted = 0
                 AND size_bytes >= ?
               GROUP BY sha256
               HAVING cnt > 1
               ORDER BY total_size DESC""",
            (min_size,),
        ) as cur:
            hashes = await cur.fetchall()

        groups = []
        for h in hashes:
            async with db.execute(
                """SELECT id, path, filename, size_bytes, modified_at, accessed_at
                   FROM files_metadata
                   WHERE sha256 = ? AND is_deleted = 0""",
                (h["sha256"],),
            ) as cur:
                files = [dict(r) for r in await cur.fetchall()]

            groups.append({
                "sha256":      h["sha256"],
                "count":       h["cnt"],
                "wasted_bytes": h["total_size"] - files[0]["size_bytes"],
                "files":       files,
            })

    return groups


async def get_duplicate_summary() -> dict:
    """High-level stats about duplicates."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT COUNT(*) as dup_groups,
                      SUM(file_count - 1) as extra_files
               FROM hash_index WHERE file_count > 1"""
        ) as cur:
            row = await cur.fetchone()

        async with db.execute(
            """SELECT SUM(size_bytes) as wasted
               FROM files_metadata f
               JOIN hash_index h ON f.sha256 = h.sha256
               WHERE h.file_count > 1 AND f.is_deleted = 0"""
        ) as cur:
            wasted = await cur.fetchone()

    return {
        "duplicate_groups": row["dup_groups"] or 0,
        "extra_files":      row["extra_files"] or 0,
        "wasted_bytes":     wasted["wasted"] or 0,
    }
