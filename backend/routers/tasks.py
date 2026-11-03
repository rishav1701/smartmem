"""routers/tasks.py"""
from fastapi import APIRouter, HTTPException, Query
import json

from workers.task_queue import create_task, enqueue, get_task, get_all_tasks, cancel_task

router = APIRouter()


@router.get("/tasks")
async def list_tasks(limit: int = Query(default=10, ge=1, le=200)):
    return await get_all_tasks(limit)


@router.get("/tasks/{task_id}")
async def task_detail(task_id: str):
    t = await get_task(task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


@router.post("/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str):
    ok = await cancel_task(task_id)
    if not ok:
        raise HTTPException(400, "Task cannot be cancelled (already done/failed/cancelled)")
    return {"status": "cancelled", "task_id": task_id}


@router.post("/tasks/{task_id}/retry")
async def retry_task_endpoint(task_id: str):
    """Create a fresh attempt for a failed persisted task."""
    previous = await get_task(task_id)
    if not previous:
        raise HTTPException(404, "Task not found")
    if previous["status"] != "failed":
        raise HTTPException(409, "Only failed tasks can be retried")

    try:
        payload = json.loads(previous.get("payload") or "{}")
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    if previous["task_type"] == "scan":
        from services.scanner.file_scanner import scan_directory
        path = payload.get("path")
        if not path:
            raise HTTPException(422, "Scan task has no saved directory")
        args, kwargs, handler = (path,), {}, scan_directory
    elif previous["task_type"] == "hash":
        from services.scanner.file_scanner import hash_all_files
        args, kwargs, handler = (), {}, hash_all_files
    elif previous["task_type"] == "embed":
        from services.search.semantic_search import build_index
        args, kwargs, handler = (bool(payload.get("force", False)),), {}, build_index
    elif previous["task_type"] == "categorize":
        from services.organizer.ai_organizer import categorize_files
        args = (bool(payload.get("dry_run", True)), payload.get("base_dir", ""),
                bool(payload.get("force", False)))
        kwargs, handler = {}, categorize_files
    else:
        raise HTTPException(422, "This task type cannot be retried")

    retry_id = await create_task(previous["task_type"], payload)
    await enqueue(retry_id, handler, *args, **kwargs)
    return {"status": "queued", "task_id": retry_id, "retry_of": task_id}
