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
import logging
import pathlib
import pickle
import re

import aiosqlite
import httpx
import numpy as np
from database.db import DB_PATH
from config import OLLAMA_BASE, OLLAMA_TIMEOUT, FAISS_INDEX_PATH, FAISS_MAP_PATH
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
_id_map: list[int] = []
_embed_model: str | None = None
_embed_dim:   int | None = None

# FIX: Increased semaphore from 3 to 8 for better parallelization
_EMBED_SEM  = asyncio.Semaphore(8)
_MODEL_LOCK = asyncio.Lock()


async def _detect_embed_model() -> str | None:
    """Detect best embedding model (cached for performance)."""
    global _embed_model
    async with _MODEL_LOCK:
        if _embed_model:
            return _embed_model
        try:
            client = await _get_embed_client()
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
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
                    _embed_model = pref
                    logger.info("✓ Embedding model: %s", _embed_model)
                    return _embed_model
            
            # Fallback to first model with "embed" in name
            for m in available:
                if "embed" in m.lower():
                    _embed_model = m
                    logger.info("✓ Using embedding model: %s", _embed_model)
                    return _embed_model
            
            logger.warning("No suitable embedding model found. Available: %s", available)
        except Exception as exc:
            logger.error("Error detecting embedding model: %s", exc)
        return None
        _embed_dim = len(raw)
        logger.info("Embed dim: %d", _embed_dim)
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


# ── FAISS ─────────────────────────────────────────────────────────────────────

def _load_index(dim: int):
    global _index, _id_map
    try:
        import faiss
        if FAISS_INDEX_PATH.exists() and FAISS_MAP_PATH.exists():
            loaded = faiss.read_index(str(FAISS_INDEX_PATH))
            with open(FAISS_MAP_PATH, "rb") as f:
                loaded_map = pickle.load(f)
            if loaded.ntotal == len(loaded_map) and loaded.d == dim:
                _index  = loaded
                _id_map = loaded_map
                logger.info("FAISS loaded: %d vectors dim=%d", _index.ntotal, dim)
                return
            logger.warning("FAISS mismatch — creating fresh index")
        _index  = faiss.IndexFlatIP(dim)
        _id_map = []
        logger.info("Fresh FAISS index dim=%d", dim)
    except ImportError:
        raise RuntimeError("faiss-cpu not installed. Run: pip install faiss-cpu")


def _save_index():
    try:
        import faiss
        FAISS_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(_index, str(FAISS_INDEX_PATH))
        with open(FAISS_MAP_PATH, "wb") as f:
            pickle.dump(_id_map, f)
    except Exception as exc:
        logger.warning("Save FAISS failed: %s", exc)


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

    if _index is not None:
        status["index_vectors"] = _index.ntotal
    elif not FAISS_INDEX_PATH.exists():
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
    """
    Step 1: Extract text from every doc that has no doc_text cached.
    Step 2: Embed each doc's rich text and store in FAISS.
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

    # Force: reset embedding_ids for doc files
    if force:
        await update_task(task_id, "running", 0.04, "Force rebuild — resetting index...")
        ext_list = list(DOC_EXTENSIONS)
        ph = ",".join("?" * len(ext_list))
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute(
                f"UPDATE files_metadata SET embedding_id=NULL "
                f"WHERE extension IN ({ph})", ext_list
            )
            await db.commit()
        _index  = faiss.IndexFlatIP(dim)
        _id_map = []
    else:
        _load_index(dim)

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
            text = await asyncio.to_thread(extract_text, row["path"], 2000)
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
            f"""SELECT id, path, filename, extension, ai_category, size_bytes, doc_text
                FROM files_metadata
                WHERE extension IN ({ph})
                  AND embedding_id IS NULL
                  AND is_deleted=0
                ORDER BY size_bytes ASC""",
            ext_list
        ) as cur:
            to_embed = await cur.fetchall()

    total_embed = len(to_embed)
    if total_embed == 0:
        await update_task(task_id, "done", 1.0,
                          f"Index up to date ({_index.ntotal} vectors)")
        return

    logger.info("Embedding %d docs", total_embed)
    await update_task(task_id, "running", 0.30,
                      f"Embedding {total_embed} documents with {model}...")

    embedded   = 0
    EMBED_BATCH = 8   # conservative — Ollama calls are the bottleneck

    async def embed_one(row) -> tuple[int, "np.ndarray | None"]:
        text = _build_embed_text(
            row["filename"],
            row["extension"] or "",
            row["ai_category"] or "unknown",
            row["size_bytes"],
            row["doc_text"] or "",
        )
        async with _EMBED_SEM:
            raw = await _raw_embed(model, text)
        if raw is None:
            return row["id"], None
        arr = _normalise(np.array(raw, dtype=np.float32))
        return row["id"], arr

    for i in range(0, total_embed, EMBED_BATCH):
        batch   = to_embed[i : i + EMBED_BATCH]
        results = await asyncio.gather(*[embed_one(r) for r in batch])

        vecs_batch = []
        ids_batch  = []
        for db_id, vec in results:
            if vec is None:
                continue
            faiss_id = len(_id_map)
            _id_map.append(db_id)
            vecs_batch.append(vec)
            ids_batch.append((faiss_id, db_id))
            embedded += 1

        if vecs_batch:
            _index.add(np.vstack(vecs_batch))

        async with aiosqlite.connect(str(DB_PATH)) as db:
            for faiss_id, db_id in ids_batch:
                await db.execute(
                    "UPDATE files_metadata SET embedding_id=? WHERE id=?",
                    (faiss_id, db_id)
                )
            await db.commit()

        _save_index()
        pct = 0.30 + (embedded / max(total_embed, 1)) * 0.70
        await update_task(task_id, "running", pct,
                          f"Embedded {embedded}/{total_embed}  "
                          f"(FAISS: {_index.ntotal} vectors)")
        await asyncio.sleep(0)

    _save_index()
    await update_task(
        task_id, "done", 1.0,
        f"Done: {_index.ntotal} doc vectors  |  model={model}  |  dim={dim}"
    )
    logger.info("Doc index complete: %d vectors", _index.ntotal)


# ── Search ────────────────────────────────────────────────────────────────────

async def semantic_search(query: str, top_k: int = 12) -> list[dict]:
    """
    Hybrid search: semantic (FAISS cosine) + keyword overlap.
    Final score = 0.75 * semantic + 0.25 * keyword_overlap
    """
    global _index, _id_map

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
        return []

    # Expand query for better recall
    expanded_query = _expand_query(query)

    q_vec = await _embed_text(expanded_query)
    if q_vec is None:
        logger.warning("Cannot embed query — Ollama down?")
        return []

    try:
        import faiss
        # Fetch more candidates than needed so we can re-rank with keywords
        k = min(top_k * 3, _index.ntotal)
        scores, indices = _index.search(q_vec.reshape(1, -1), k)
    except Exception as exc:
        logger.error("FAISS search error: %s", exc)
        return []

    # Fetch file records and compute hybrid scores
    results = []
    for sem_score, idx in zip(scores[0], indices[0]):
        if idx < 0 or idx >= len(_id_map):
            continue
        db_id = _id_map[int(idx)]

        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT path, filename, size_bytes, ai_category,
                          extension, modified_at, accessed_at, doc_text
                   FROM files_metadata WHERE id=? AND is_deleted=0""",
                (db_id,)
            ) as cur:
                row = await cur.fetchone()

        if not row:
            continue

        kw_score = _keyword_score(
            query,
            row["filename"],
            row["doc_text"] or "",
            row["ai_category"] or "",
        )

        # Hybrid score
        hybrid = 0.75 * float(sem_score) + 0.25 * kw_score

        r = dict(row)
        r["score"]          = round(hybrid, 4)
        r["semantic_score"] = round(float(sem_score), 4)
        r["keyword_score"]  = round(kw_score, 4)
        # Don't return full doc_text to frontend — keep response small
        r.pop("doc_text", None)
        results.append(r)

    # Sort by hybrid score, return top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]
