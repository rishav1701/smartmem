"""Read-only application and privacy settings for the local UI."""
from fastapi import APIRouter, Depends
import aiosqlite

from config import (APP_VERSION, DATA_DIR, DB_PATH, OLLAMA_BASE, DOCUMENT_MAX_CHARS,
                    CHUNK_SIZE, CHUNK_OVERLAP)
from database.db import get_db

router = APIRouter()


@router.get("/settings")
async def settings(db: aiosqlite.Connection = Depends(get_db)):
    async with db.execute("SELECT path, alias, last_scanned FROM directories ORDER BY path") as cur:
        directories = [dict(row) for row in await cur.fetchall()]
    return {
        "version": APP_VERSION,
        "ollama_endpoint": OLLAMA_BASE,
        "database_path": str(DB_PATH),
        "index_path": str(DATA_DIR),
        "document_character_limit": DOCUMENT_MAX_CHARS,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "directories": directories,
        "external_static_assets": ["fonts.googleapis.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com"],
    }
