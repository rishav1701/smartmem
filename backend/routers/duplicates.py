"""routers/duplicates.py"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import aiosqlite
from database.db import get_db
from security.guard import safe_delete, assert_under_allowed, resolve_safe
from services.duplicate.duplicate_finder import get_duplicates, get_duplicate_summary

router = APIRouter()


@router.get("/duplicates")
async def list_duplicates(min_size: int = 1024):
    summary = await get_duplicate_summary()
    groups  = await get_duplicates(min_size)
    return {"summary": summary, "groups": groups}


class DeleteIn(BaseModel):
    path: str


@router.delete("/delete-file")
async def delete_file(body: DeleteIn, db: aiosqlite.Connection = Depends(get_db)):
    p = resolve_safe(body.path)
    await assert_under_allowed(p, db)
    if not p.exists():
        raise HTTPException(404, "File not found")

    recycle_path = safe_delete(p)

    await db.execute(
        "UPDATE files_metadata SET is_deleted=1 WHERE path=?", (str(p),)
    )
    await db.commit()
    return {"status": "moved_to_recycle", "recycle_path": str(recycle_path)}
