"""routers/search.py"""
from fastapi import APIRouter, Query
from workers.task_queue import create_task, enqueue
from services.search.semantic_search import (
    semantic_search, build_index, check_ollama_available, get_search_status
)

router = APIRouter()


@router.get("/semantic-search")
async def search(q: str = Query(min_length=1, max_length=1000),
                 top_k: int = Query(default=10, ge=1, le=50)):
    if not q.strip():
        return {"results": [], "query": q, "count": 0}
    results = await semantic_search(q, top_k)
    return {"query": q, "results": results, "count": len(results)}


@router.post("/build-index")
async def trigger_index_build(force: bool = False):
    """
    Queue an embedding build task.
    force=True builds a fresh index generation and only switches to it after saving.
    """
    tid = await create_task("embed", {"force": force})
    await enqueue(tid, build_index, force)
    return {"status": "queued", "task_id": tid, "force": force}


@router.get("/search-status")
async def search_status():
    """Full diagnostic — tells the UI exactly what's wrong if search isn't working."""
    return await get_search_status()


@router.get("/ollama-status")
async def ollama_status():
    return await check_ollama_available()
