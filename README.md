# SMARTMEM – Personal AI Memory Assistant

> **Privacy-first local AI storage optimization system.**
> Zero data leaves your machine. All AI runs via Ollama.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        Browser (UI)                          │
│  HTML + CSS + JS · D3 Sunburst · Chart.js · Semantic Search  │
└─────────────────────────────┬────────────────────────────────┘
                              │ HTTP (localhost:8000)
┌─────────────────────────────▼────────────────────────────────┐
│                    FastAPI Backend                            │
│                                                              │
│  Routers: directories · scan · storage · duplicates          │
│           large_files · organizer · search · tasks           │
│                                                              │
│  Services:                                                   │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │  Scanner  │ │Duplicate │ │Organizer │ │   Search     │  │
│  │ os.walk() │ │ SHA-256  │ │  Ollama  │ │Ollama Embed  │  │
│  │ metadata  │ │hash index│ │   LLM    │ │+ FAISS index │  │
│  └───────────┘ └──────────┘ └──────────┘ └──────────────┘  │
│                                                              │
│  Workers: Async task queue · progress tracking · retry       │
│  Security: Path guard · safe-delete · access control         │
│                                                              │
│  ┌──────────────────┐     ┌──────────────────────────────┐  │
│  │   SQLite DB      │     │       FAISS Index            │  │
│  │  files_metadata  │     │   nomic-embed-text vectors   │  │
│  │  directories     │     │   768-dim flat L2            │  │
│  │  tasks           │     └──────────────────────────────┘  │
│  │  organizer_log   │                                        │
│  │  hash_index      │                                        │
│  └──────────────────┘                                        │
└──────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼────────────────────────────────┐
│                       Ollama (local)                         │
│   llama3.2        — file categorization (AI organizer)       │
│   nomic-embed-text — embedding generation (semantic search)  │
└──────────────────────────────────────────────────────────────┘
```

---

## Folder Structure

```
smartmem/
├── frontend/
│   └── index.html              # Complete single-file dashboard UI
│
└── backend/
    ├── main.py                 # FastAPI app entry point
    ├── requirements.txt
    ├── data/                   # Auto-created: SQLite + FAISS index
    │
    ├── database/
    │   └── db.py               # Schema, init, aiosqlite helpers
    │
    ├── routers/
    │   ├── directories.py      # POST /select-directory, GET /directories
    │   ├── scan.py             # POST /scan, POST /hash-files
    │   ├── storage.py          # GET /storage-analysis
    │   ├── duplicates.py       # GET /duplicates, DELETE /delete-file
    │   ├── large_files.py      # GET /large-files
    │   ├── organizer.py        # POST /organize, POST /organize/undo
    │   ├── search.py           # GET /semantic-search, POST /build-index
    │   └── tasks.py            # GET /tasks, GET /tasks/{id}
    │
    ├── services/
    │   ├── scanner/
    │   │   └── file_scanner.py # os.walk, metadata upsert, SHA-256
    │   ├── duplicate/
    │   │   └── duplicate_finder.py
    │   ├── organizer/
    │   │   └── ai_organizer.py # Ollama LLM categorization + undo
    │   ├── search/
    │   │   └── semantic_search.py # Ollama embed + FAISS
    │   └── analytics/
    │       └── storage_analyzer.py
    │
    ├── workers/
    │   └── task_queue.py       # Async queue, status tracking
    │
    └── security/
        └── guard.py            # Path validation, safe-delete
```

---

## Database Schema

```sql
-- User-selected root directories
directories(id, path UNIQUE, alias, added_at, last_scanned)

-- All scanned file metadata
files_metadata(
  id, path UNIQUE, filename, extension,
  size_bytes, created_at, modified_at, accessed_at,
  sha256,           -- populated by hash worker
  ai_category,      -- populated by AI organizer
  embedding_id,     -- FAISS vector index
  scanned_at, is_deleted
)

-- Dedup lookup table
hash_index(sha256 PK, file_count)

-- Background job tracking
tasks(id UUID, task_type, status, progress, message, created_at, updated_at, payload)

-- Undo log for file moves
organizer_log(id, original_path, new_path, moved_at, undone)

-- App config
config(key, value)
```

---

## Setup & Run Instructions

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Backend runtime |
| Ollama | latest | Local AI |
| Node | (optional) | Dev server only |

---

### Step 1 – Install Ollama

```bash
# macOS / Linux
curl -fsSL https://ollama.ai/install.sh | sh

# Pull required models
ollama pull llama3.2           # AI file organizer (~2GB)
ollama pull nomic-embed-text   # Semantic search embeddings (~270MB)

# Verify Ollama is running
ollama serve &
curl http://localhost:11434/api/tags
```

---

### Step 2 – Install Python dependencies

```bash
cd smartmem/backend

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
```

> **Note on faiss-cpu:** If installation fails on your platform, try:
> ```bash
> conda install -c conda-forge faiss-cpu   # via conda
> # OR: pip install faiss-cpu --index-url https://pypi.org/simple
> ```

---

### Step 3 – Run the server

```bash
cd smartmem/backend
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Open your browser: **http://localhost:8000**

---

### Step 4 – First use workflow

```
1. Directories tab   → Enter /home/user/Downloads → Add
2. Directories tab   → Click "Scan All"
3. Task Queue tab    → Wait for scan to complete
4. Duplicates tab    → Click "Hash Files" (topbar)
5. Task Queue tab    → Wait for hashing to complete
6. Duplicates tab    → View + delete duplicates
7. Organizer tab     → Run in Dry Run mode first
8. Search tab        → Click "Build Index" → wait
9. Search tab        → Type natural language query
```

---

## API Reference

```
POST /api/select-directory    body: {path, alias}
GET  /api/directories
DELETE /api/directories/{id}

POST /api/scan                body: {path?}
POST /api/hash-files
GET  /api/scan-status/{task_id}

GET  /api/storage-analysis

GET  /api/duplicates?min_size=1024
DELETE /api/delete-file       body: {path}

GET  /api/large-files?min_mb=100&unused_days=90

POST /api/organize            body: {dry_run, base_dir}
POST /api/organize/undo?count=10

GET  /api/semantic-search?q=query&top_k=10
POST /api/build-index

GET  /api/tasks?limit=20
GET  /api/tasks/{task_id}
```

Interactive docs: **http://localhost:8000/docs**

---

## Security Model

| Threat | Mitigation |
|--------|-----------|
| Path traversal | `resolve_safe()` blocks `..` and null bytes |
| Out-of-scope access | `assert_under_allowed()` enforces directory whitelist |
| Accidental hard-delete | `safe_delete()` moves to `~/.smartmem_recycle` |
| External data exfiltration | Zero external HTTP calls; all AI runs via Ollama locally |
| Permission escalation | Inherits user-level OS permissions only |

---

## Performance Tuning

| Scenario | Config |
|----------|--------|
| Large drives (>500k files) | Increase batch commit size in `file_scanner.py` line ~57 |
| Slow Ollama | Reduce parallel tasks in `task_queue.py` `num_workers` |
| GPU acceleration | Install `faiss-gpu` instead of `faiss-cpu` |
| Incremental scans | Already supported — re-scan only touches changed `mtime` files |

---

## Future Scalability

1. **Multi-user support** — Add JWT auth + per-user directory namespacing
2. **Celery + Redis** — Replace in-process queue for multi-process deployments
3. **Real-time WebSocket** — Push task progress to frontend without polling
4. **Cloud backup integration** — Optional S3/rclone sync of organised files
5. **Plugin system** — Custom AI categorization rules per user
6. **Mobile companion** — React Native app reading the same SQLite via sync
7. **Scheduled scans** — APScheduler cron jobs for nightly incremental indexing
8. **Plugin extractors** — PDF/DOCX/image content extraction for richer embeddings
9. **LLM chat interface** — Natural language queries like "find all tax docs from 2023"
10. **Deduplication streaming** — Stream hash results via SSE for very large drives

---

*Built for Hackathon — 100% local, 0 bytes sent to cloud.*
