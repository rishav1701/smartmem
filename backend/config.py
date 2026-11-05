"""
backend/config.py – Centralized configuration with environment variable support
"""
import pathlib
import os

# ── Base directories ──────────────────────────────────────────────────────────
BASE_DIR = pathlib.Path(__file__).parent
APP_VERSION = "0.3.0"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = pathlib.Path(os.getenv("SMARTMEM_DB_PATH", str(DATA_DIR / "smartmem.db"))).expanduser()
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── External services ─────────────────────────────────────────────────────────
OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "60"))  # seconds

# ── Storage & file operations ─────────────────────────────────────────────────
RECYCLE_BIN = pathlib.Path(os.getenv(
    "SMARTMEM_RECYCLE_BIN",
    str(pathlib.Path.home() / ".smartmem_recycle")
)).expanduser()

# ── FAISS indexing ────────────────────────────────────────────────────────────
FAISS_INDEX_PATH = DATA_DIR / "faiss_docs.index"
FAISS_MAP_PATH = DATA_DIR / "faiss_docs_map.pkl"
FAISS_MANIFEST_PATH = DATA_DIR / "faiss_docs_manifest.json"
FAISS_INDEX_MAX_RETRIES = 3
DOCUMENT_MAX_CHARS = min(max(int(os.getenv("MEMORA_DOCUMENT_MAX_CHARS", "100000")), 1000), 2_000_000)
CHUNK_SIZE = min(max(int(os.getenv("MEMORA_CHUNK_SIZE", "1200")), 200), 8000)
CHUNK_OVERLAP = min(max(int(os.getenv("MEMORA_CHUNK_OVERLAP", "160")), 0), CHUNK_SIZE // 2)

# ── Task queue ────────────────────────────────────────────────────────────────
TASK_CANCELLED_RETENTION_DAYS = int(os.getenv("TASK_CANCELLED_RETENTION_DAYS", "7"))
TASK_CLEANUP_INTERVAL_HOURS = int(os.getenv("TASK_CLEANUP_INTERVAL_HOURS", "1"))

# ── Security ──────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = [origin.strip() for origin in os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:8000"
).split(",") if origin.strip()]

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# ── Feature flags ─────────────────────────────────────────────────────────────
ENABLE_OLLAMA = os.getenv("ENABLE_OLLAMA", "true").lower() == "true"
OLLAMA_AUTOSTART = os.getenv("OLLAMA_AUTOSTART", "true").lower() == "true"
ENABLE_FAISS = os.getenv("ENABLE_FAISS", "true").lower() == "true"
