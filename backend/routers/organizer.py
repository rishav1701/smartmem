"""routers/organizer.py"""
import pathlib
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import aiosqlite
from workers.task_queue import create_task, enqueue
from services.organizer.ai_organizer import (
    categorize_files, undo_last_moves, get_model_status
)
from security.guard import validate_directory, assert_under_allowed
from database.db import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


class OrganizeIn(BaseModel):
    dry_run:  bool = True
    base_dir: str  = ""
    force:    bool = False


@router.post("/organize")
async def organize(body: OrganizeIn, db: aiosqlite.Connection = Depends(get_db)):
    """
    Start a file categorization task.
    FIX: Properly validate base_dir for path traversal attacks.
    """
    if not body.dry_run:
        if not body.base_dir.strip():
            raise HTTPException(400, "base_dir required when dry_run=False")
        
        # FIX: Use validate_directory() from security/guard.py
        # This checks for symlinks, permissions, and proper path structure
        try:
            validated_dir = validate_directory(body.base_dir.strip())
            # Also verify it's under allowed directories
            await assert_under_allowed(validated_dir, db)
        except HTTPException as e:
            logger.error("Directory validation failed: %s", e.detail)
            raise

    tid = await create_task(
        "categorize",
        {
            "dry_run": body.dry_run,
            "base_dir": body.base_dir,
            "force": body.force
        }
    )
    await enqueue(tid, categorize_files, body.dry_run, body.base_dir, body.force)
    return {
        "status": "queued",
        "task_id": tid,
        "dry_run": body.dry_run,
        "force": body.force
    }


@router.post("/organize/undo")
async def undo_organize(count: int = 10):
    results = await undo_last_moves(count)
    ok  = sum(1 for r in results if r["status"] == "ok")
    return {"restored": ok, "results": results}


@router.get("/organize/model")
async def model_status():
    return await get_model_status()


@router.get("/organize/log")
async def organizer_log(limit: int = 50, db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute(
        "SELECT * FROM organizer_log ORDER BY moved_at DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]
