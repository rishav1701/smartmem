"""
workers/task_queue.py - Async background task queue with cancel support
"""
import asyncio, json, uuid, logging
from typing import Callable
from datetime import datetime, timedelta
import aiosqlite
from database.db import DB_PATH
from config import TASK_CANCELLED_RETENTION_DAYS

logger = logging.getLogger(__name__)

_queue: asyncio.Queue = asyncio.Queue()
_loop_started = False

# FIX: Use a dict to map task_id -> cancellation timestamp (for cleanup)
# instead of a plain set that grows forever
_cancelled: dict[str, datetime] = {}
_cancelled_lock = asyncio.Lock()


async def _cleanup_old_cancelled():
    """
    Periodically remove cancelled task IDs older than retention period.
    Prevents unbounded memory growth.
    """
    while True:
        try:
            await asyncio.sleep(3600)  # Run every hour
            cutoff = datetime.now() - timedelta(days=TASK_CANCELLED_RETENTION_DAYS)
            async with _cancelled_lock:
                to_remove = [
                    tid for tid, ts in _cancelled.items()
                    if ts < cutoff
                ]
                for tid in to_remove:
                    del _cancelled[tid]
                if to_remove:
                    logger.info("Cleaned up %d expired cancellations", len(to_remove))
        except Exception as exc:
            logger.warning("Error in cancellation cleanup: %s", exc)


async def create_task(task_type: str, payload: dict = None) -> str:
    task_id = str(uuid.uuid4())
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(
            "INSERT INTO tasks(id, task_type, status, payload, created_at, updated_at) "
            "VALUES (?, ?, 'queued', ?, datetime('now'), datetime('now'))",
            (task_id, task_type, json.dumps(payload or {})),
        )
        await db.commit()
    return task_id


async def update_task(task_id: str, status: str, progress: float = None, message: str = None):
    async with aiosqlite.connect(str(DB_PATH)) as db:
        fields = ["status=?", "updated_at=datetime('now')"]
        values = [status]
        if progress is not None:
            fields.append("progress=?"); values.append(progress)
        if message is not None:
            fields.append("message=?"); values.append(message)
        values.append(task_id)
        await db.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
        await db.commit()


async def cancel_task(task_id: str) -> bool:
    """Mark a queued/running task as cancelled. Returns True if it was cancellable."""
    async with _cancelled_lock:
        _cancelled[task_id] = datetime.now()
    
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT status FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
        if not row or row["status"] in ("done", "failed", "cancelled"):
            return False
        await db.execute(
            "UPDATE tasks SET status='cancelled', updated_at=datetime('now'), "
            "message='Cancelled by user' WHERE id=?", (task_id,)
        )
        await db.commit()
    logger.info("Task %s cancelled", task_id)
    return True


def is_cancelled(task_id: str) -> bool:
    return task_id in _cancelled


async def get_task(task_id: str) -> dict | None:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)) as cur:
            row = await cur.fetchone()
    return dict(row) if row else None


async def get_all_tasks(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def enqueue(task_id: str, coro_fn: Callable, *args, **kwargs):
    await _queue.put((task_id, coro_fn, args, kwargs))
    logger.info("Enqueued task %s (%s)", task_id, coro_fn.__name__)


async def _worker():
    while True:
        task_id, coro_fn, args, kwargs = await _queue.get()
        # FIX: Double-check cancellation status before running
        if is_cancelled(task_id):
            await update_task(task_id, "cancelled", 0.0, "Cancelled before execution")
            _queue.task_done()
            continue
        logger.info("Running task %s", task_id)
        await update_task(task_id, "running", progress=0.0)
        try:
            await coro_fn(task_id, *args, **kwargs)
            if not is_cancelled(task_id):
                await update_task(task_id, "done", progress=1.0)
            logger.info("Task %s complete", task_id)
        except asyncio.CancelledError:
            logger.info("Task %s was cancelled during execution", task_id)
            await update_task(task_id, "cancelled", message="Cancelled during execution")
        except Exception as exc:
            logger.exception("Task %s failed: %s", task_id, exc)
            await update_task(task_id, "failed", message=str(exc))
        finally:
            _queue.task_done()


def start_worker(num_workers: int = 2):
    global _loop_started
    if _loop_started:
        return
    # Start cleanup routine
    asyncio.create_task(_cleanup_old_cancelled())
    for _ in range(num_workers):
        asyncio.create_task(_worker())
    _loop_started = True
    logger.info("%d background workers started", num_workers)
