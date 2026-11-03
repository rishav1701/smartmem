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
    """Summarize active duplicate groups from file rows, not stale hash counters."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT COUNT(*) AS duplicate_groups,
                      COALESCE(SUM(file_count - 1), 0) AS extra_files,
                      COALESCE(SUM(file_size * (file_count - 1)), 0) AS wasted_bytes
               FROM (
                   SELECT sha256, COUNT(*) AS file_count, MAX(size_bytes) AS file_size
                   FROM files_metadata
                   WHERE sha256 IS NOT NULL AND is_deleted=0
                   GROUP BY sha256
                   HAVING COUNT(*) > 1
               )"""
        ) as cur:
            row = dict(await cur.fetchone())

    return {
        "duplicate_groups": row["duplicate_groups"],
        "extra_files": row["extra_files"],
        "wasted_bytes": row["wasted_bytes"],
    }
