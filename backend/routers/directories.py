"""routers/directories.py"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import aiosqlite
from database.db import get_db
from security.guard import validate_directory

router = APIRouter()


class DirectoryIn(BaseModel):
    path: str
    alias: str = Field(default="", max_length=255)  # FIX: Add validation


@router.post("/select-directory")
async def select_directory(body: DirectoryIn, db: aiosqlite.Connection = Depends(get_db)):
    p = validate_directory(body.path)
    
    # Sanitize alias
    alias = body.alias.strip() if body.alias else p.name
    if any(c in alias for c in ["\x00", "\n", "\r", "\t"]):
        raise HTTPException(400, "Alias contains invalid characters")
    
    await db.execute(
        "INSERT INTO directories(path, alias) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET alias=excluded.alias",
        (str(p), alias),
    )
    await db.commit()
    return {"status": "ok", "path": str(p), "alias": alias}


@router.get("/directories")
async def list_directories(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT * FROM directories ORDER BY added_at DESC") as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


@router.delete("/directories/{dir_id}")
async def remove_directory(dir_id: int, db: aiosqlite.Connection = Depends(get_db)):
    await db.execute("DELETE FROM directories WHERE id=?", (dir_id,))
    await db.commit()
    return {"status": "removed"}
