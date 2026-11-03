"""
backend/config.py – Centralized configuration with environment variable support
"""
import pathlib
import os
from typing import Optional

# ── Base directories ──────────────────────────────────────────────────────────
BASE_DIR = pathlib.Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = pathlib.Path(os.getenv("SMARTMEM_DB_PATH", str(DATA_DIR / "smartmem.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── External services ─────────────────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))  # seconds

# ── Storage & file operations ─────────────────────────────────────────────────
RECYCLE_BIN = pathlib.Path(os.getenv(
    "SMARTMEM_RECYCLE_BIN",
    str(pathlib.Path.home() / ".smartmem_recycle")
))
RECYCLE_BIN.mkdir(parents=True, exist_ok=True)

# ── FAISS indexing ────────────────────────────────────────────────────────────
FAISS_INDEX_PATH = DATA_DIR / "faiss_docs.index"
FAISS_MAP_PATH = DATA_DIR / "faiss_docs_map.pkl"
FAISS_INDEX_MAX_RETRIES = 3

# ── Task queue ────────────────────────────────────────────────────────────────
TASK_CANCELLED_RETENTION_DAYS = int(os.getenv("TASK_CANCELLED_RETENTION_DAYS", "7"))
TASK_CLEANUP_INTERVAL_HOURS = int(os.getenv("TASK_CLEANUP_INTERVAL_HOURS", "1"))

# ── Security ──────────────────────────────────────────────────────────────────
ALLOW_ALL_ORIGINS = os.getenv("ALLOW_ALL_ORIGINS", "false").lower() == "true"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:8000").split(",")

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Feature flags ─────────────────────────────────────────────────────────────
ENABLE_OLLAMA = os.getenv("ENABLE_OLLAMA", "true").lower() == "true"
ENABLE_FAISS = os.getenv("ENABLE_FAISS", "true").lower() == "true"
