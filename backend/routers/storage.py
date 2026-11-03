"""routers/storage.py"""
from fastapi import APIRouter, Depends, Query
import aiosqlite
from database.db import get_db
from services.analytics.storage_analyzer import (
    get_storage_tree, get_extension_breakdown, get_top_level_stats,
    get_disk_usage, get_category_size_breakdown
)

router = APIRouter()


@router.get("/storage-analysis")
async def storage_analysis():
    tree   = await get_storage_tree()
    exts   = await get_extension_breakdown()
    stats  = await get_top_level_stats()
    cats   = await get_category_size_breakdown()
    disks  = await get_disk_usage()
    return {"tree": tree, "extensions": exts, "stats": stats,
            "categories": cats, "disks": disks}


@router.get("/disk-usage")
async def disk_usage():
    return await get_disk_usage()


@router.get("/categories")
async def category_counts(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT ai_category, COUNT(*) as count "
        "FROM files_metadata WHERE is_deleted=0 "
        "GROUP BY ai_category ORDER BY count DESC"
    ) as cur:
        rows = await cur.fetchall()
    counts = {r["ai_category"] or "Uncategorised": r["count"] for r in rows}
    return {"categories": counts, "total_classified": sum(counts.values())}


@router.get("/categorised-files")
async def categorised_files(
    category: str = None,
    parent: str = None,   # top-level filter e.g. "Academics" matches all Academics/*
    limit: int = Query(default=200, ge=1, le=1000),
    db: aiosqlite.Connection = Depends(get_db)
):
    """
    Return files filtered by AI category.
    category = exact match e.g. "Academics/Lectures"
    parent   = prefix match e.g. "Academics" → all Academics/* sub-categories
    """
    if category:
        async with db.execute(
            "SELECT path,filename,size_bytes,extension,ai_category,modified_at "
            "FROM files_metadata WHERE ai_category=? AND is_deleted=0 "
            "ORDER BY size_bytes DESC LIMIT ?",
            (category, limit)
        ) as cur:
            rows = await cur.fetchall()
    elif parent:
        async with db.execute(
            "SELECT path,filename,size_bytes,extension,ai_category,modified_at "
            "FROM files_metadata "
            "WHERE (ai_category=? OR ai_category LIKE ?) AND is_deleted=0 "
            "ORDER BY ai_category, size_bytes DESC LIMIT ?",
            (parent, f"{parent}/%", limit)
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT path,filename,size_bytes,extension,ai_category,modified_at "
            "FROM files_metadata WHERE ai_category IS NOT NULL AND is_deleted=0 "
            "ORDER BY ai_category, size_bytes DESC LIMIT ?",
            (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]
