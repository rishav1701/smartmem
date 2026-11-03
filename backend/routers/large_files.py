"""routers/large_files.py"""
from fastapi import APIRouter, Depends
import aiosqlite
from database.db import get_db
from datetime import datetime, timedelta

router = APIRouter()


@router.get("/large-files")
async def large_files(
    min_mb: float = 100,
    unused_days: int = 90,
    limit: int = 50,
    db: aiosqlite.Connection = Depends(get_db),
):
    min_bytes = int(min_mb * 1024 * 1024)
    cutoff = (datetime.now() - timedelta(days=unused_days)).isoformat()

    async with db.execute(
        """SELECT path, filename, size_bytes, accessed_at, modified_at, extension, ai_category
           FROM files_metadata
           WHERE size_bytes >= ?
             AND is_deleted = 0
           ORDER BY size_bytes DESC
           LIMIT ?""",
        (min_bytes, limit),
    ) as cur:
        all_large = [dict(r) for r in await cur.fetchall()]

    # FIX: Proper NULL handling
    # Empty string "" is always < any ISO datetime string, causing false positives
    # Use explicit None check and proper comparison
    for f in all_large:
        accessed = f["accessed_at"]
        # Mark as unused if:
        # 1. Never accessed (accessed_at is NULL)
        # 2. Last accessed before cutoff
        f["unused"] = accessed is None or accessed < cutoff

    return {
        "threshold_mb":  min_mb,
        "unused_days":   unused_days,
        "files":         all_large,
        "total_bytes":   sum(f["size_bytes"] for f in all_large),
    }
