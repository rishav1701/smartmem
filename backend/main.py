"""
SMARTMEM - Personal AI Memory Assistant
Main FastAPI Application
"""

import logging
import pathlib
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from database.db import init_db
from workers.task_queue import start_worker, recover_interrupted_tasks
from config import LOG_LEVEL, ALLOW_ALL_ORIGINS, ALLOWED_ORIGINS, DATA_DIR, APP_VERSION
from routers import (
    directories, scan, storage, duplicates,
    large_files, organizer, search, tasks, file_actions, settings
)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("memora")

DATA_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Memora starting up ...")
    await init_db()
    recovered = await recover_interrupted_tasks()
    if recovered:
        logger.warning("Marked %d interrupted task(s) failed; they can be retried", recovered)
    start_worker(num_workers=2)
    logger.info("Background workers started")
    yield
    logger.info("Memora shutting down ...")
    # Cleanup Ollama connections
    from services.organizer import ai_organizer
    from services.search import semantic_search
    await ai_organizer._close_ollama_client()
    await semantic_search._close_embed_client()
    logger.info("✓ Ollama connections closed")


app = FastAPI(
    title="Memora - Local AI Memory",
    version=APP_VERSION,
    description="Local-first file intelligence and semantic search",
    lifespan=lifespan,
)

# Fixed CORS configuration using config values
cors_origins = ["*"] if ALLOW_ALL_ORIGINS else ALLOWED_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

app.include_router(directories.router, prefix="/api", tags=["Directories"])
app.include_router(scan.router,        prefix="/api", tags=["Scanning"])
app.include_router(storage.router,     prefix="/api", tags=["Storage"])
app.include_router(duplicates.router,  prefix="/api", tags=["Duplicates"])
app.include_router(large_files.router, prefix="/api", tags=["Large Files"])
app.include_router(organizer.router,   prefix="/api", tags=["Organizer"])
app.include_router(search.router,      prefix="/api", tags=["Search"])
app.include_router(tasks.router,       prefix="/api", tags=["Tasks"])
app.include_router(file_actions.router, prefix="/api", tags=["Files"])
app.include_router(settings.router, prefix="/api", tags=["Settings"])


# ── System metrics endpoint (inspired by uploaded main.py / psutil pattern) ───
@app.get("/api/system-metrics")
async def system_metrics():
    """
    Real-time system metrics using psutil.
    Mirrors the uploaded main.py's /storage pattern, extended.
    """
    try:
        import psutil

        # CPU
        cpu_pct  = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count()

        # Memory
        mem = psutil.virtual_memory()

        # Primary disk (home drive)
        home = pathlib.Path.home()
        disk = psutil.disk_usage(str(home))

        # Disk I/O (cumulative since boot)
        try:
            io = psutil.disk_io_counters()
            read_bytes  = io.read_bytes  if io else 0
            write_bytes = io.write_bytes if io else 0
        except Exception:
            read_bytes = write_bytes = 0

        return {
            "cpu": {
                "percent":      cpu_pct,
                "cores":        cpu_count,
            },
            "memory": {
                "total_gb":    round(mem.total   / 1024**3, 2),
                "used_gb":     round(mem.used    / 1024**3, 2),
                "free_gb":     round(mem.available / 1024**3, 2),
                "percent_used": mem.percent,
            },
            "disk": {
                "total_gb":    round(disk.total / 1024**3, 2),
                "used_gb":     round(disk.used  / 1024**3, 2),
                "free_gb":     round(disk.free  / 1024**3, 2),
                "percent_used": disk.percent,
            },
            "io": {
                "total_read_gb":  round(read_bytes  / 1024**3, 2),
                "total_write_gb": round(write_bytes / 1024**3, 2),
            },
        }
    except ImportError:
        return {"error": "psutil not installed — run: pip install psutil"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": APP_VERSION}


# Serve frontend
FRONTEND_DIR = pathlib.Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_ui():
        return FileResponse(str(FRONTEND_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
