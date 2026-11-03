"""routers/scan.py"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import aiosqlite
from database.db import get_db
from workers.task_queue import create_task, get_task, enqueue, get_all_tasks
from services.scanner.file_scanner import scan_directory, hash_all_files

router = APIRouter()


class ScanIn(BaseModel):
    path: str | None = None   # scan a specific path; if None, scan all saved dirs


@router.post("/scan")
async def start_scan(body: ScanIn, db: aiosqlite.Connection = Depends(get_db)):
    if body.path:
        paths = [body.path]
    else:
        async with db.execute("SELECT path FROM directories") as cur:
            rows = await cur.fetchall()
        paths = [r["path"] for r in rows]

    if not paths:
        raise HTTPException(400, "No directories configured. Add one first.")

    task_ids = []
    for p in paths:
        tid = await create_task("scan", {"path": p})
        await enqueue(tid, scan_directory, p)
        task_ids.append(tid)

    return {"status": "queued", "task_ids": task_ids}


@router.post("/hash-files")
async def start_hashing():
    tid = await create_task("hash")
    await enqueue(tid, hash_all_files)
    return {"status": "queued", "task_id": tid}


@router.get("/scan-status/{task_id}")
async def scan_status(task_id: str):
    task = await get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task
