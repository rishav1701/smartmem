"""
services/search/semantic_search.py
-------------------------------------
Document semantic search: Ollama embeddings + FAISS.

Key improvements over previous version:
  1. Real content extraction — PDF via pypdf, DOCX via python-docx,
     XLSX via openpyxl, CSV, HTML, code files, Jupyter notebooks
  2. doc_text cached in SQLite — never re-extracts on rebuild
  3. Rich embed text (filename + category + extracted content up to 2000 chars)
  4. Keyword pre-filter before FAISS — narrows candidate set, speeds up search
  5. Hybrid scoring: 70% semantic + 30% keyword match
  6. Psutil-based disk metrics exposed via status endpoint
  7. Query expansion — adds synonyms / related terms before embedding
"""

import asyncio
import json
import logging
import os
import pathlib
import re
import uuid

import aiosqlite
import httpx
import numpy as np
from database.db import DB_PATH
from config import (OLLAMA_BASE, OLLAMA_TIMEOUT, FAISS_INDEX_PATH, FAISS_MAP_PATH,
                    FAISS_MANIFEST_PATH, DATA_DIR, DOCUMENT_MAX_CHARS,
                    CHUNK_SIZE, CHUNK_OVERLAP)
from workers.task_queue import update_task
from services.search.document_extractor import extract_text

logger = logging.getLogger(__name__)

# ── Optimizations ────────────────────────────────────────────────────────────
_OLLAMA_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()

# Model preferences ordered by speed (embedding models should be fast)
EMBED_MODEL_PREFS = [
    "nomic-embed-text", "nomic-embed-text:latest",
    "mxbai-embed-large", "mxbai-embed-large:latest",
    "all-minilm", "all-minilm:latest",
    "bge-small", "bge-small:latest",
]

async def _get_embed_client() -> httpx.AsyncClient:
    """Get or create persistent HTTP client for embeddings (connection pooling)."""
    global _OLLAMA_CLIENT
    if _OLLAMA_CLIENT is None:
        async with _CLIENT_LOCK:
            if _OLLAMA_CLIENT is None:
                _OLLAMA_CLIENT = httpx.AsyncClient(
                    timeout=OLLAMA_TIMEOUT,
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
                logger.info("✓ Embed client initialized with connection pooling")
    return _OLLAMA_CLIENT

async def _close_embed_client():
    """Close persistent client on shutdown."""
    global _OLLAMA_CLIENT
    if _OLLAMA_CLIENT:
        await _OLLAMA_CLIENT.aclose()
        _OLLAMA_CLIENT = None
        logger.info("✓ Embed client closed")

# ── Document types to index ───────────────────────────────────────────────────
DOC_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".odt", ".rtf", ".pages",
    ".ppt", ".pptx", ".odp", ".key",
    ".xls", ".xlsx", ".ods", ".csv", ".numbers",
    ".txt", ".md", ".rst", ".tex", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".c", ".cpp", ".cs", ".go", ".rs",
    ".rb", ".php", ".sh", ".html", ".css", ".sql",
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg",
    ".ipynb",
}

_index        = None
_id_map: list[int | dict] = []
_embed_model: str | None = None
_embed_dim:   int | None = None
_embed_dim_model: str | None = None
_INDEX_LOCK = asyncio.Lock()
_index_building = False

# FIX: Increased semaphore from 3 to 8 for better parallelization
_EMBED_SEM  = asyncio.Semaphore(8)
_MODEL_LOCK = asyncio.Lock()


async def _detect_embed_model() -> str | None:
    """Detect best embedding model (cached for performance)."""
    global _embed_model, _embed_dim, _embed_dim_model
    async with _MODEL_LOCK:
        if _embed_model:
            return _embed_model
        try:
            client = await _get_embed_client()
            r = await asyncio.wait_for(
                client.get(f"{OLLAMA_BASE}/api/tags"), timeout=5
            )
            if r.status_code != 200:
                logger.warning("Failed to fetch Ollama models: %d", r.status_code)
                return None
            available = [m["name"] for m in r.json().get("models", [])]
            if not available:
                logger.warning("No embedding models available in Ollama")
                return None
            
            # Pick fastest available model
            for pref in EMBED_MODEL_PREFS:
                if pref in available:
                    if _embed_model != pref:
                        _embed_dim = None
                        _embed_dim_model = None
                    _embed_model = pref
                    logger.info("✓ Embedding model: %s", _embed_model)
                    return _embed_model
            
            # Fallback to first model with "embed" in name
            for m in available:
                if "embed" in m.lower():
                    if _embed_model != m:
                        _embed_dim = None
                        _embed_dim_model = None
                    _embed_model = m
                    logger.info("✓ Using embedding model: %s", _embed_model)
                    return _embed_model
            
            logger.warning("No suitable embedding model found. Available: %s", available)
        except Exception as exc:
            logger.error("Error detecting embedding model: %s", exc)
        return None


async def _detect_dim(model: str) -> int | None:
    """Probe the configured Ollama model once to learn its vector dimension."""
    global _embed_dim, _embed_dim_model
    if _embed_dim is not None and _embed_dim_model == model:
        return _embed_dim
    raw = await _raw_embed(model, "Memora embedding dimension probe")
    if not raw:
        return None
    _embed_dim = len(raw)
    _embed_dim_model = model
    logger.info("Embedding dimension detected: %d", _embed_dim)
    return _embed_dim


# ── Embedding ─────────────────────────────────────────────────────────────────

async def _raw_embed(model: str, text: str) -> list[float] | None:
    """Get embeddings from Ollama (optimized with connection pooling)."""
    if not text or not text.strip():
        return None
    
    # New Ollama API (faster)
    try:
        client = await _get_embed_client()
        r = await client.post(
            f"{OLLAMA_BASE}/api/embed",
            json={"model": model, "input": text}
        )
        if r.status_code == 200:
            vecs = r.json().get("embeddings")
            if vecs and len(vecs[0]) > 0:
                return vecs[0]
    except Exception:
        pass
    
    # Legacy Ollama API (slower fallback)
    try:
        client = await _get_embed_client()
        r = await client.post(
            f"{OLLAMA_BASE}/api/embeddings",
            json={"model": model, "prompt": text}
        )
        if r.status_code == 200:
            vec = r.json().get("embedding")
            if vec and len(vec) > 0:
                return vec
    except Exception as exc:
        logger.debug("Embedding failed: %s", exc)
    
    return None


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


async def _embed_text(text: str) -> "np.ndarray | None":
    model = await _detect_embed_model()
    if not model:
        return None
    async with _EMBED_SEM:
        raw = await _raw_embed(model, text)
    if raw is None:
        return None
    return _normalise(np.array(raw, dtype=np.float32))


# ── Rich embed text builder ───────────────────────────────────────────────────

def _build_embed_text(filename: str, extension: str,
                      category: str, size_bytes: int,
                      doc_text: str) -> str:
    """
    Build a rich semantic string for embedding.
    Using structured sections that embedding models handle well.
    """
    stem = pathlib.Path(filename).stem.replace("_", " ").replace("-", " ")

    parts = [
        f"Title: {stem}",
        f"Filename: {filename}",
        f"Type: {extension or 'document'}",
    ]

    if category and category not in ("unknown", "Others", "Uncategorised"):
        parts.append(f"Category: {category}")

    if size_bytes:
        if size_bytes >= 1_000_000:
            parts.append(f"Size: {size_bytes // 1_000_000}MB")
        elif size_bytes >= 1_000:
            parts.append(f"Size: {size_bytes // 1_000}KB")

    if doc_text and doc_text.strip():
        # Put content last — embedding models weight earlier tokens more
        parts.append(f"Content: {doc_text.strip()}")

    return "\n".join(parts)


# ── Query expansion ───────────────────────────────────────────────────────────

_SYNONYMS: dict[str, list[str]] = {
    "report":    ["analysis", "summary", "document", "findings"],
    "notes":     ["lecture", "notes", "summary", "study", "material"],
    "invoice":   ["bill", "receipt", "payment", "tax"],
    "resume":    ["cv", "curriculum vitae", "portfolio"],
    "budget":    ["finance", "expenses", "cost", "spending"],
    "project":   ["work", "task", "development", "implementation"],
    "meeting":   ["minutes", "agenda", "discussion", "notes"],
    "research":  ["paper", "study", "findings", "analysis"],
    "code":      ["script", "program", "source", "implementation"],
    "data":      ["dataset", "spreadsheet", "table", "records"],
    "manual":    ["guide", "documentation", "instructions", "handbook"],
    "contract":  ["agreement", "legal", "document", "terms"],
}


def _expand_query(query: str) -> str:
    """Add related terms to improve recall."""
    q_lower = query.lower()
    extra = []
    for word, synonyms in _SYNONYMS.items():
        if word in q_lower:
            extra.extend(synonyms[:2])
    if extra:
        return f"{query} {' '.join(extra)}"
    return query


# ── Keyword pre-filter ────────────────────────────────────────────────────────

def _keyword_score(query: str, filename: str, doc_text: str, category: str) -> float:
    """
    Quick keyword overlap score [0..1].
    Used as a tiebreaker and to filter obviously irrelevant results.
    """
    terms = set(re.findall(r'\w+', query.lower()))
    if not terms:
        return 0.0
    target = f"{filename} {doc_text or ''} {category or ''}".lower()
    target_words = set(re.findall(r'\w+', target))
    hits = sum(1 for t in terms if any(t in w for w in target_words))
    return hits / len(terms)


def _chunk_document(text: str, size: int = CHUNK_SIZE,
                    overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split normalized extracted text into bounded overlapping passages."""
    text = re.sub(r"\s+", " ", text or "").strip()
    size = max(200, min(int(size), 8000))
    overlap = max(0, min(int(overlap), size // 2))
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = max(text.rfind(". ", start + size // 2, end),
                           text.rfind("; ", start + size // 2, end))
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _make_snippet(text: str, query: str, max_chars: int = 280) -> str:
    """Return a literal excerpt near a query term; never synthesize source text."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= max_chars:
        return text
    terms = [term for term in re.findall(r"\w+", query.lower()) if len(term) > 1]
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) >= 0]
    start = max(0, (min(positions) if positions else 0) - max_chars // 4)
    end = min(len(text), start + max_chars)
    excerpt = text[start:end]
    return ("…" if start else "") + excerpt + ("…" if end < len(text) else "")


async def _lexical_search(query: str, top_k: int) -> list[dict]:
    """SQLite lexical fallback used when vector search or Ollama is unavailable."""
    terms = list(dict.fromkeys(re.findall(r"[\w-]{2,}", query.lower())))[:12]
    if not terms:
        return []
    predicates = " OR ".join(
        "LOWER(filename) LIKE ? OR LOWER(COALESCE(doc_text, '')) LIKE ?"
        for _ in terms
    )
    params = [pattern for term in terms for pattern in (f"%{term}%", f"%{term}%")]
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, path, filename, size_bytes, ai_category, extension, "
            "modified_at, accessed_at, doc_text FROM files_metadata "
            f"WHERE is_deleted=0 AND ({predicates}) LIMIT ?",
            (*params, min(max(top_k * 20, 100), 500)),
        ) as cur:
            rows = await cur.fetchall()
    results = []
    for row in rows:
        item = dict(row)
        score = _keyword_score(query, item["filename"], item["doc_text"] or "",
                               item["ai_category"] or "")
        if score <= 0:
            continue
        item["score"] = round(score, 4)
        item["semantic_score"] = None
        item["keyword_score"] = round(score, 4)
        item["snippet"] = _make_snippet(item.pop("doc_text") or item["filename"], query)
        matched = [term for term in terms if term in (
            (item["filename"] + " " + item["snippet"]).lower())]
        item["match_reason"] = "Text match: " + ", ".join(matched[:4])
        results.append(item)
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top_k]


# ── FAISS ─────────────────────────────────────────────────────────────────────

def _load_index(dim: int) -> bool:
    global _index, _id_map
    try:
        import faiss
        if FAISS_MANIFEST_PATH.exists():
            manifest = json.loads(FAISS_MANIFEST_PATH.read_text(encoding="utf-8"))
            index_name, map_name = manifest.get("index"), manifest.get("map")
            if not all(isinstance(name, str) and pathlib.Path(name).name == name
                       for name in (index_name, map_name)):
                raise ValueError("Invalid FAISS generation manifest")
            if (manifest.get("model") and _embed_model
                    and manifest["model"] != _embed_model):
                raise ValueError("Saved index uses a different embedding model")
            generation_index = DATA_DIR / index_name
            generation_map = DATA_DIR / map_name
            loaded = faiss.read_index(str(generation_index))
            loaded_map = json.loads(generation_map.read_text(encoding="utf-8"))
            if isinstance(loaded_map, list) and loaded.ntotal == len(loaded_map) and loaded.d == dim:
                _index, _id_map = loaded, loaded_map
                logger.info("FAISS generation loaded: %d vectors dim=%d", loaded.ntotal, dim)
                return True
            raise ValueError("FAISS generation manifest does not match its files")
        if FAISS_INDEX_PATH.exists() and FAISS_MAP_PATH.exists():
            # Legacy maps used pickle, which can execute code when loaded.
            # Preserve those files for manual recovery, but rebuild safely in JSON.
            logger.warning("Ignoring legacy pickle FAISS map; a safe JSON generation will be built")
        _index  = faiss.IndexFlatIP(dim)
        _id_map = []
        logger.info("Fresh FAISS index dim=%d", dim)
        return False
    except ImportError:
        raise RuntimeError("faiss-cpu not installed. Run: pip install faiss-cpu")
    except Exception as exc:
        logger.warning("Cannot load saved FAISS generation; rebuilding: %s", exc)
        _index = faiss.IndexFlatIP(dim)
        _id_map = []
        return False


def _save_index():
    try:
        import faiss
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        index_name = f"faiss_docs_{generation}.index"
        map_name = f"faiss_docs_{generation}.json"
        index_tmp = DATA_DIR / f".{index_name}.tmp"
        map_tmp = DATA_DIR / f".{map_name}.tmp"
        manifest_tmp = DATA_DIR / ".faiss_docs_manifest.tmp"
        faiss.write_index(_index, str(index_tmp))
        map_tmp.write_text(json.dumps(_id_map, ensure_ascii=False), encoding="utf-8")
        os.replace(index_tmp, DATA_DIR / index_name)
        os.replace(map_tmp, DATA_DIR / map_name)
        manifest_tmp.write_text(json.dumps({
            "index": index_name, "map": map_name,
            "model": _embed_model, "dimension": _index.d,
        }), encoding="utf-8")
        os.replace(manifest_tmp, FAISS_MANIFEST_PATH)
        return True
    except Exception as exc:
        logger.warning("Save FAISS failed: %s", exc)
        return False


# ── Status ────────────────────────────────────────────────────────────────────

async def get_search_status() -> dict:
    status = {
        "faiss_installed":       False,
        "ollama_running":        False,
        "embed_model":           None,
        "embed_model_available": False,
        "index_vectors":         0,
        "index_dim":             _embed_dim,
        "db_doc_files":          0,
        "db_embedded_files":     0,
        "db_total_files":        0,
        "db_with_text":          0,
        "ready":                 False,
        "issues":                [],
    }

    try:
        import faiss
        status["faiss_installed"] = True
    except ImportError:
        status["issues"].append("faiss-cpu not installed — run: pip install faiss-cpu")

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_BASE}/api/tags")
        if r.status_code == 200:
            status["ollama_running"] = True
            models = [m["name"] for m in r.json().get("models", [])]
            for pref in EMBED_MODEL_PREFS:
                if pref in models:
                    status["embed_model"] = pref
                    status["embed_model_available"] = True
                    break
            if not status["embed_model_available"]:
                for m in models:
                    if "embed" in m.lower():
                        status["embed_model"] = m
                        status["embed_model_available"] = True
                        break
            if not status["embed_model_available"]:
                status["issues"].append(
                    "No embedding model — run: ollama pull nomic-embed-text"
                )
    except Exception as exc:
        status["issues"].append(f"Ollama offline: {exc}  →  run: ollama serve")

    if _index is None and status["embed_model_available"]:
        try:
            dim = await _detect_dim(status["embed_model"])
            if dim:
                _load_index(dim)
        except Exception as exc:
            status["issues"].append(f"Index load error: {exc}")

    if _index is not None:
        status["index_vectors"] = _index.ntotal
    elif not FAISS_MANIFEST_PATH.exists() and not FAISS_INDEX_PATH.exists():
        status["issues"].append(
            "No index yet — click 'Build Doc Index' after scanning"
        )

    try:
        ext_list     = list(DOC_EXTENSIONS)
        placeholders = ",".join("?" * len(ext_list))
        async with aiosqlite.connect(str(DB_PATH)) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM files_metadata "
                f"WHERE extension IN ({placeholders}) AND is_deleted=0", ext_list
            ) as c:
                status["db_doc_files"] = (await c.fetchone())[0]
            async with db.execute(
                f"SELECT COUNT(*) FROM files_metadata "
                f"WHERE extension IN ({placeholders}) AND embedding_id IS NOT NULL "
                f"AND is_deleted=0", ext_list
            ) as c:
                status["db_embedded_files"] = (await c.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM files_metadata WHERE is_deleted=0"
            ) as c:
                status["db_total_files"] = (await c.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM files_metadata "
                "WHERE doc_text IS NOT NULL AND doc_text != '' AND is_deleted=0"
            ) as c:
                status["db_with_text"] = (await c.fetchone())[0]
    except Exception as exc:
        status["issues"].append(f"DB error: {exc}")

    if (status["faiss_installed"] and status["embed_model_available"]
            and status["index_vectors"] > 0):
        status["ready"] = True

    return status


async def check_ollama_available() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{OLLAMA_BASE}/api/tags")
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            return {"running": True, "models": models}
    except Exception as exc:
        return {"running": False, "error": str(exc)}
    return {"running": False}


# ── Build index ───────────────────────────────────────────────────────────────

async def build_index(task_id: str, force: bool = False):
    """Serialize index writers and keep searches on lexical fallback while building."""
    global _index, _id_map, _index_building
    async with _INDEX_LOCK:
        _index_building = True
        try:
            await _build_index_impl(task_id, force)
        except Exception:
            # Persisted generation remains authoritative until the manifest is
            # replaced. Drop in-memory partial state so the next search reloads it.
            _index = None
            _id_map = []
            raise
        finally:
            _index_building = False


async def _build_index_impl(task_id: str, force: bool = False):
    """
    Extract text when needed, embed overlapping passages, then publish a new
    index generation before updating SQLite's first-vector references.
    """
    global _index, _id_map

    await update_task(task_id, "running", 0.01, "Detecting embed model...")
    model = await _detect_embed_model()
    if not model:
        await update_task(task_id, "failed", 0,
                          "No embed model. Run: ollama pull nomic-embed-text")
        return

    dim = await _detect_dim(model)
    if dim is None:
        await update_task(task_id, "failed", 0,
                          f"Cannot detect embedding dim from {model}. Is Ollama running?")
        return

    await update_task(task_id, "running", 0.03, f"Model: {model}  dim={dim}")

    try:
        import faiss
    except ImportError:
        await update_task(task_id, "failed", 0,
                          "faiss-cpu not installed. Run: pip install faiss-cpu")
        return

    rebuild_all = force
    if rebuild_all:
        await update_task(task_id, "running", 0.04, "Preparing a new index generation...")
        _index  = faiss.IndexFlatIP(dim)
        _id_map = []
    else:
        if not _load_index(dim):
            rebuild_all = True

    # ── Step 1: Extract text for docs that don't have it yet ──────────────────
    ext_list = list(DOC_EXTENSIONS)
    ph       = ",".join("?" * len(ext_list))
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT id, path, filename, extension
                FROM files_metadata
                WHERE extension IN ({ph})
                  AND (doc_text IS NULL OR doc_text = '')
                  AND is_deleted=0""",
            ext_list
        ) as cur:
            needs_extraction = await cur.fetchall()

    total_extract = len(needs_extraction)
    if total_extract > 0:
        await update_task(task_id, "running", 0.05,
                          f"Extracting text from {total_extract} documents...")
        logger.info("Extracting text from %d files", total_extract)

        EXTRACT_BATCH = 20

        async def do_extract(row) -> tuple[int, str]:
            text = await asyncio.to_thread(extract_text, row["path"], DOCUMENT_MAX_CHARS)
            return row["id"], text

        for i in range(0, total_extract, EXTRACT_BATCH):
            batch   = needs_extraction[i : i + EXTRACT_BATCH]
            results = await asyncio.gather(*[do_extract(r) for r in batch])

            async with aiosqlite.connect(str(DB_PATH)) as db:
                for db_id, text in results:
                    await db.execute(
                        "UPDATE files_metadata SET doc_text=? WHERE id=?",
                        (text, db_id)
                    )
                await db.commit()

            pct = 0.05 + (min(i + EXTRACT_BATCH, total_extract) / total_extract) * 0.25
            await update_task(task_id, "running", pct,
                              f"Extracted {min(i+EXTRACT_BATCH,total_extract)}/{total_extract}")
            await asyncio.sleep(0)

    # ── Step 2: Embed docs that aren't in FAISS yet ───────────────────────────
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"""SELECT id, path, filename, extension, ai_category, size_bytes,
                       modified_at, doc_text
                FROM files_metadata
                WHERE extension IN ({ph})
                  AND (? OR embedding_id IS NULL)
                  AND is_deleted=0
                ORDER BY size_bytes ASC""",
            (*ext_list, int(rebuild_all))
        ) as cur:
            to_embed = await cur.fetchall()

    total_embed = len(to_embed)
    if total_embed == 0:
        if rebuild_all:
            if not _save_index():
                _index, _id_map = None, []
                await update_task(task_id, "failed", 0, "Could not save the empty search index")
                return
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await db.execute(
                    f"UPDATE files_metadata SET embedding_id=NULL WHERE extension IN ({ph})",
                    ext_list,
                )
                await db.commit()
        await update_task(task_id, "done", 1.0,
                          f"Index up to date ({_index.ntotal} vectors)")
        return

    logger.info("Embedding %d docs", total_embed)
    await update_task(task_id, "running", 0.30,
                      f"Embedding {total_embed} documents with {model}...")

    embedded   = 0
    processed_files = 0
    EMBED_BATCH = 8   # conservative — Ollama calls are the bottleneck

    async def embed_one(row) -> tuple[int, list[tuple[str, np.ndarray]]]:
        text = row["doc_text"] or ""
        chunks = _chunk_document(text) or [""]
        embedded_chunks = []
        for chunk in chunks:
            embed_text = _build_embed_text(
                row["filename"], row["extension"] or "",
                row["ai_category"] or "unknown", row["size_bytes"], chunk,
            )
            async with _EMBED_SEM:
                raw = await _raw_embed(model, embed_text)
            if raw is None:
                continue
            vec = _normalise(np.array(raw, dtype=np.float32))
            embedded_chunks.append((chunk, vec))
        return row["id"], embedded_chunks

    first_vector_by_file: dict[int, int] = {}
    for i in range(0, total_embed, EMBED_BATCH):
        batch   = to_embed[i : i + EMBED_BATCH]
        results = await asyncio.gather(*[embed_one(r) for r in batch])

        vecs_batch = []
        row_by_id = {row["id"]: row for row in batch}
        for db_id, chunks in results:
            for chunk, vec in chunks:
                faiss_id = len(_id_map)
                if db_id not in first_vector_by_file:
                    first_vector_by_file[db_id] = faiss_id
                file_row = row_by_id[db_id]
                _id_map.append({
                    "file_id": db_id,
                    "chunk_text": chunk,
                    "modified_at": file_row["modified_at"],
                    "size_bytes": file_row["size_bytes"],
                })
                vecs_batch.append(vec)
                embedded += 1

        if vecs_batch:
            _index.add(np.vstack(vecs_batch))

        processed_files += len(batch)
        pct = 0.30 + (processed_files / max(total_embed, 1)) * 0.70
        await update_task(task_id, "running", pct,
                          f"Processed {processed_files}/{total_embed} files; embedded {embedded} passages  "
                          f"(FAISS: {_index.ntotal} vectors)")
        await asyncio.sleep(0)

    if not _save_index():
        _index, _id_map = None, []
        await update_task(task_id, "failed", 0, "Could not save the new search index")
        return
    async with aiosqlite.connect(str(DB_PATH)) as db:
        if rebuild_all:
            await db.execute(
                f"UPDATE files_metadata SET embedding_id=NULL WHERE extension IN ({ph})",
                ext_list,
            )
        for db_id, faiss_id in first_vector_by_file.items():
            await db.execute("UPDATE files_metadata SET embedding_id=? WHERE id=?",
                             (faiss_id, db_id))
        await db.commit()
    await update_task(
        task_id, "done", 1.0,
        f"Done: {_index.ntotal} passage vectors  |  model={model}  |  dim={dim}"
    )
    logger.info("Doc index complete: %d vectors", _index.ntotal)


# ── Search ────────────────────────────────────────────────────────────────────

async def semantic_search(query: str, top_k: int = 12) -> list[dict]:
    """
    Hybrid search: semantic (FAISS cosine) + keyword overlap.
    Final score = 0.75 * semantic + 0.25 * keyword_overlap
    """
    global _index, _id_map
    query = (query or "").strip()[:1000]
    top_k = min(max(int(top_k), 1), 50)
    if not query:
        return []
    if _index_building:
        return await _lexical_search(query, top_k)

    # Lazy-load index
    if _index is None:
        model = await _detect_embed_model()
        dim   = await _detect_dim(model) if model else None
        if dim:
            try:
                _load_index(dim)
            except Exception as exc:
                logger.error("Cannot load index: %s", exc)
                return []

    if _index is None or _index.ntotal == 0:
        logger.warning("FAISS doc index empty — run Build Doc Index")
        return await _lexical_search(query, top_k)

    index_snapshot, map_snapshot = _index, _id_map

    # Expand query for better recall
    expanded_query = _expand_query(query)

    q_vec = await _embed_text(expanded_query)
    if q_vec is None:
        logger.warning("Cannot embed query — Ollama down?")
        return await _lexical_search(query, top_k)
    if _index_building or _index is not index_snapshot:
        return await _lexical_search(query, top_k)

    try:
        import faiss
        # Fetch more candidates than needed so we can re-rank with keywords
        k = min(top_k * 10, index_snapshot.ntotal)
        scores, indices = index_snapshot.search(q_vec.reshape(1, -1), k)
    except Exception as exc:
        logger.error("FAISS search error: %s", exc)
        return await _lexical_search(query, top_k)

    # Fetch file records and compute hybrid scores. Passage maps from earlier
    # generations are ignored when the source metadata changed.
    candidates = []
    for sem_score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(map_snapshot):
            continue
        entry = map_snapshot[int(idx)]
        if isinstance(entry, dict):
            db_id = entry.get("file_id")
        elif isinstance(entry, int):
            db_id = entry
        else:
            continue
        if not isinstance(db_id, int):
            continue
        candidates.append((sem_score, int(idx), entry, db_id))

    file_ids = sorted({candidate[3] for candidate in candidates})
    rows_by_id = {}
    if file_ids:
        placeholders = ",".join("?" for _ in file_ids)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id, embedding_id, path, filename, size_bytes, ai_category,
                          extension, modified_at, accessed_at, doc_text
                   FROM files_metadata WHERE is_deleted=0 AND id IN ("""
                + placeholders + ")",
                file_ids,
            ) as cur:
                rows_by_id = {row["id"]: row for row in await cur.fetchall()}

    results_by_file = {}
    for sem_score, idx, entry, db_id in candidates:
        row = rows_by_id.get(db_id)
        if not row:
            continue
        if isinstance(entry, dict) and (
            entry.get("modified_at") != row["modified_at"]
            or entry.get("size_bytes") != row["size_bytes"]
        ):
            continue
        if isinstance(entry, int) and row["embedding_id"] != int(idx):
            continue

        kw_score = _keyword_score(
            query,
            row["filename"],
            row["doc_text"] or "",
            row["ai_category"] or "",
        )

        # Hybrid score
        semantic_score = min(1.0, max(0.0, float(sem_score)))
        hybrid = 0.75 * semantic_score + 0.25 * kw_score

        r = dict(row)
        r["score"]          = round(hybrid, 4)
        r["semantic_score"] = round(semantic_score, 4)
        r["keyword_score"]  = round(kw_score, 4)
        r["snippet"] = _make_snippet(
            entry.get("chunk_text", "") if isinstance(entry, dict)
            else r["doc_text"] or r["filename"], query
        )
        matched = [term for term in re.findall(r"[\w-]{2,}", query.lower())
                   if term in ((r["filename"] + " " + (r["doc_text"] or "")).lower())]
        r["match_reason"] = (
            "Text match: " + ", ".join(dict.fromkeys(matched[:4]))
            if matched else "Semantic similarity to indexed document content"
        )
        # Don't return full doc_text to frontend — keep response small
        r.pop("doc_text", None)
        current = results_by_file.get(db_id)
        if current is None or r["score"] > current["score"]:
            results_by_file[db_id] = r

    # Sort by hybrid score, return top_k
    results = sorted(results_by_file.values(), key=lambda x: x["score"], reverse=True)
    lexical_results = await _lexical_search(query, top_k * 2)
    for item in lexical_results:
        file_id = item["id"]
        if file_id in results_by_file:
            continue
        item["score"] = round(0.25 * item["keyword_score"], 4)
        item["match_reason"] = "Lexical match in filename or extracted text"
        results.append(item)
    results.sort(key=lambda item: item["score"], reverse=True)
    return results[:top_k]
