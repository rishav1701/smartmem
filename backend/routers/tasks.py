"""routers/tasks.py"""
from fastapi import APIRouter, HTTPException
from workers.task_queue import get_task, get_all_tasks, cancel_task

router = APIRouter()


@router.get("/tasks")
async def list_tasks(limit: int = 10):   # default 10 as requested
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
