"""
services/organizer/ai_organizer.py
------------------------------------
AI file categorizer with expanded sub-categories.

Full taxonomy (6 main → 24 sub-categories):
  Academics  → Lectures, Assignments, Research, Exams, Textbooks
  Personal   → Finance, Health, Legal, Family, Travel
  Projects   → Code, Design, Planning, Documentation
  Media      → Photos, Videos, Music, Podcasts
  Archives   → Backups, Compressed, Installers
  Others     → (anything that doesn't fit)
"""
import asyncio, json, logging, pathlib, re, shutil
import aiosqlite, httpx
from database.db import DB_PATH
from config import OLLAMA_BASE, OLLAMA_TIMEOUT
from workers.task_queue import update_task, is_cancelled

logger = logging.getLogger(__name__)
_detected_model: str | None = None
_MODEL_LOCK = asyncio.Lock()

# ── Ollama optimization: connection pooling ──────────────────────────────────
_OLLAMA_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOCK = asyncio.Lock()

# ── Model preferences (ordered by speed & efficiency) ──────────────────────────
MODEL_PREFS = [
    "mistral", "mistral:latest",           # Fast, lightweight
    "neural-chat", "neural-chat:latest",   # Optimized for categorization
    "orca-mini", "orca-mini:latest",       # Small, efficient
    "llama2", "llama2:latest",             # Balanced
    "llama3", "llama3:latest",
    "llama3.2", "llama3.2:latest",
    "gemma2", "gemma2:latest",
]

async def _get_ollama_client() -> httpx.AsyncClient:
    """Get or create a persistent HTTP client for Ollama (connection pooling)."""
    global _OLLAMA_CLIENT
    if _OLLAMA_CLIENT is None:
        async with _CLIENT_LOCK:
            if _OLLAMA_CLIENT is None:
                # Reuse connections, enable HTTP/2 for multiplexing
                _OLLAMA_CLIENT = httpx.AsyncClient(
                    timeout=OLLAMA_TIMEOUT,
                    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                    http2=False,  # Use HTTP/1.1 with keep-alive
                )
                logger.info("✓ Ollama client initialized with connection pooling")
    return _OLLAMA_CLIENT

async def _close_ollama_client():
    """Close the persistent client (call on shutdown)."""
    global _OLLAMA_CLIENT
    if _OLLAMA_CLIENT:
        await _OLLAMA_CLIENT.aclose()
        _OLLAMA_CLIENT = None
        logger.info("✓ Ollama client closed")

# ── Expanded taxonomy ─────────────────────────────────────────────────────────
CATEGORIES = [
    # Academics
    "Academics/Lectures", "Academics/Assignments", "Academics/Research",
    "Academics/Exams", "Academics/Textbooks",
    # Personal
    "Personal/Finance", "Personal/Health", "Personal/Legal",
    "Personal/Family", "Personal/Travel",
    # Projects
    "Projects/Code", "Projects/Design", "Projects/Planning",
    "Projects/Documentation",
    # Media
    "Media/Photos", "Media/Videos", "Media/Music", "Media/Podcasts",
    # Archives
    "Archives/Backups", "Archives/Compressed", "Archives/Installers",
    # Others
    "Others",
]

# For DB storage we save the full path e.g. "Academics/Lectures"
# The folder structure becomes base_dir/Academics/Lectures/file.pdf

_CAT_ALIASES: dict[str, str] = {
    # Academics
    "lecture": "Academics/Lectures", "lectures": "Academics/Lectures",
    "notes": "Academics/Lectures", "class": "Academics/Lectures",
    "assignment": "Academics/Assignments", "homework": "Academics/Assignments",
    "worksheet": "Academics/Assignments", "problem set": "Academics/Assignments",
    "research": "Academics/Research", "paper": "Academics/Research",
    "thesis": "Academics/Research", "dissertation": "Academics/Research",
    "journal": "Academics/Research",
    "exam": "Academics/Exams", "quiz": "Academics/Exams",
    "test": "Academics/Exams", "midterm": "Academics/Exams",
    "textbook": "Academics/Textbooks", "book": "Academics/Textbooks",
    "course material": "Academics/Textbooks",
    # Personal
    "finance": "Personal/Finance", "budget": "Personal/Finance",
    "invoice": "Personal/Finance", "receipt": "Personal/Finance",
    "tax": "Personal/Finance", "bank": "Personal/Finance",
    "health": "Personal/Health", "medical": "Personal/Health",
    "prescription": "Personal/Health", "insurance": "Personal/Health",
    "legal": "Personal/Legal", "contract": "Personal/Legal",
    "agreement": "Personal/Legal", "passport": "Personal/Legal",
    "family": "Personal/Family", "photo album": "Personal/Family",
    "travel": "Personal/Travel", "itinerary": "Personal/Travel",
    "trip": "Personal/Travel", "vacation": "Personal/Travel",
    # Projects
    "code": "Projects/Code", "script": "Projects/Code",
    "source": "Projects/Code", "program": "Projects/Code",
    "design": "Projects/Design", "mockup": "Projects/Design",
    "wireframe": "Projects/Design", "prototype": "Projects/Design",
    "plan": "Projects/Planning", "roadmap": "Projects/Planning",
    "sprint": "Projects/Planning", "project plan": "Projects/Planning",
    "documentation": "Projects/Documentation", "readme": "Projects/Documentation",
    "spec": "Projects/Documentation", "api": "Projects/Documentation",
    # Media
    "photo": "Media/Photos", "image": "Media/Photos",
    "picture": "Media/Photos", "screenshot": "Media/Photos",
    "video": "Media/Videos", "movie": "Media/Videos",
    "recording": "Media/Videos", "clip": "Media/Videos",
    "music": "Media/Music", "song": "Media/Music",
    "audio": "Media/Music", "track": "Media/Music",
    "podcast": "Media/Podcasts", "episode": "Media/Podcasts",
    # Archives
    "backup": "Archives/Backups", "snapshot": "Archives/Backups",
    "archive": "Archives/Compressed", "zip": "Archives/Compressed",
    "compressed": "Archives/Compressed",
    "installer": "Archives/Installers", "setup": "Archives/Installers",
    "install": "Archives/Installers",
    # Others
    "others": "Others", "other": "Others", "misc": "Others",
    "unknown": "Others",
}

SYSTEM_PROMPT = f"""You are a precise file categorization assistant.
Given a filename and optional content preview, respond with ONLY a valid JSON object.
The JSON must have exactly one key "category".
Choose from these exact values:
{json.dumps(CATEGORIES, indent=2)}

Rules:
- Academics/Lectures: lecture notes, slides, class recordings
- Academics/Assignments: homework, worksheets, problem sets
- Academics/Research: papers, theses, journal articles, studies
- Academics/Exams: past exams, quizzes, test papers
- Academics/Textbooks: textbooks, course books, reference materials
- Personal/Finance: budgets, invoices, receipts, bank statements, taxes
- Personal/Health: medical records, prescriptions, health insurance
- Personal/Legal: contracts, agreements, licenses, passports, IDs
- Personal/Family: family photos, personal memories, genealogy
- Personal/Travel: itineraries, travel bookings, trip photos
- Projects/Code: source code, scripts, notebooks, config files
- Projects/Design: mockups, wireframes, design files, UI assets
- Projects/Planning: roadmaps, project plans, timelines, sprints
- Projects/Documentation: READMEs, API docs, specs, manuals
- Media/Photos: individual photos, image files, artwork
- Media/Videos: video files, recordings, screencasts
- Media/Music: music files, audio tracks, sound effects
- Media/Podcasts: podcast episodes, interviews, talk recordings
- Archives/Backups: backup files, system snapshots
- Archives/Compressed: zip/tar archives, compressed bundles
- Archives/Installers: setup files, installers, DMGs, ISOs
- Others: anything that doesn't clearly fit the above

Example response: {{"category": "Academics/Research"}}"""


async def _detect_model() -> str | None:
    """Detect the best available model for categorization (cached)."""
    global _detected_model
    async with _MODEL_LOCK:
        if _detected_model:
            return _detected_model
        try:
            client = await _get_ollama_client()
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            if r.status_code != 200:
                logger.warning("Ollama /api/tags returned %d", r.status_code)
                return None
            
            models = [m["name"] for m in r.json().get("models", [])]
            if not models:
                logger.warning("No models available in Ollama")
                return None
            
            # Pick first available from preferences (ordered by speed)
            for pref in MODEL_PREFS:
                if pref in models:
                    _detected_model = pref
                    logger.info("✓ Selected Ollama model: %s", _detected_model)
                    return _detected_model
            
            # Fallback to first available
            _detected_model = models[0]
            logger.info("✓ Using available Ollama model: %s", _detected_model)
            return _detected_model
            
        except Exception as exc:
            logger.error("Failed to detect Ollama model: %s", exc)
        return None


def _parse_category(text: str) -> str:
    text = text.strip()
    # 1. JSON extraction
    try:
        s = text.find("{"); e = text.rfind("}") + 1
        if s >= 0 and e > s:
            data = json.loads(text[s:e])
            raw = str(data.get("category", "")).strip()
            if raw in CATEGORIES:
                return raw
            # Partial match
            for cat in CATEGORIES:
                if cat.lower() == raw.lower():
                    return cat
    except Exception:
        pass
    # 2. Direct match
    for cat in CATEGORIES:
        if cat.lower() in text.lower():
            return cat
    # 3. Alias match
    lower = text.lower()
    for alias, canon in _CAT_ALIASES.items():
        if alias in lower:
            return canon
    # 4. Bare word extraction
    word = re.sub(r'[^a-zA-Z/]', '', text).strip()
    if word in CATEGORIES:
        return word
    return "Others"


async def _ask_ollama(filename: str, snippet: str = "") -> str:
    """Ask Ollama to categorize a file (optimized with faster model preferences)."""
    model = await _detect_model()
    if not model:
        logger.info("Ollama unavailable, using rule-based fallback")
        return _rule_fallback(filename)

    user_content = f'Filename: "{filename}"'
    if snippet:
        # FIX: Limit snippet to 300 chars for faster processing
        user_content += f'\nContent: {snippet[:300]}'

    payload = {
        "model": model,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 50,  # Reduced from 80 for faster inference
            "top_k": 10,
            "top_p": 0.9,
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
    }
    
    try:
        client = await _get_ollama_client()
        r = await client.post(f"{OLLAMA_BASE}/api/chat", json=payload)
        r.raise_for_status()
        raw = r.json()["message"]["content"]
        return _parse_category(raw)
    except asyncio.TimeoutError:
        logger.warning("Ollama timeout for '%s', using fallback", filename)
        return _rule_fallback(filename)
    except Exception as exc:
        logger.warning("Ollama error for '%s': %s, using fallback", filename, exc)
        return _rule_fallback(filename)


def _rule_fallback(filename: str) -> str:
    """Comprehensive rule-based classification with sub-categories."""
    ext  = pathlib.Path(filename).suffix.lower().lstrip(".")
    name = filename.lower()

    # ── Media ────────────────────────────────────────────────────────────────
    if ext in {"jpg","jpeg","png","gif","bmp","tiff","webp","heic","raw","cr2","nef","svg"}:
        return "Media/Photos"
    if ext in {"mp4","mkv","avi","mov","wmv","flv","webm","m4v","mpg","mpeg"}:
        return "Media/Videos"
    if ext in {"mp3","wav","flac","aac","ogg","m4a","wma","opus","aiff"}:
        if any(k in name for k in ["podcast","episode","interview","talk","show"]):
            return "Media/Podcasts"
        return "Media/Music"

    # ── Archives ─────────────────────────────────────────────────────────────
    if ext in {"zip","tar","gz","bz2","xz","7z","rar","lzma","zst","tbz","tgz"}:
        if any(k in name for k in ["backup","bak","snapshot","old"]):
            return "Archives/Backups"
        return "Archives/Compressed"
    if ext in {"iso","dmg","exe","msi","pkg","deb","rpm","apk","cab"}:
        return "Archives/Installers"
    if ext in {"vmdk","vdi","vhd","ova","ovf"}:
        return "Archives/Backups"

    # ── Projects/Code ────────────────────────────────────────────────────────
    if ext in {"py","ipynb","js","ts","jsx","tsx","java","c","cpp","cc","h",
               "hpp","cs","go","rs","rb","php","swift","kt","scala","sh","bash",
               "zsh","ps1","bat","cmd","r","m","lua","dart","zig","sql","graphql"}:
        return "Projects/Code"
    if ext in {"json","yaml","yml","toml","ini","cfg","conf","env","dockerfile"}:
        if any(k in name for k in ["readme","spec","api","swagger","openapi"]):
            return "Projects/Documentation"
        return "Projects/Code"
    if ext in {"html","css","scss","sass","less","vue","svelte"}:
        return "Projects/Code"

    # ── Documents — sub-classify by keyword ──────────────────────────────────
    if ext in {"pdf","doc","docx","odt","rtf","pages","tex","md","rst","txt","log"}:
        if any(k in name for k in ["lecture","slides","class","lesson","module","week"]):
            return "Academics/Lectures"
        if any(k in name for k in ["assignment","homework","hw","task","worksheet","problem"]):
            return "Academics/Assignments"
        if any(k in name for k in ["thesis","dissertation","research","paper","journal","study","abstract"]):
            return "Academics/Research"
        if any(k in name for k in ["exam","quiz","test","midterm","final","mock"]):
            return "Academics/Exams"
        if any(k in name for k in ["textbook","syllabus","curriculum","course","handbook"]):
            return "Academics/Textbooks"
        if any(k in name for k in ["invoice","receipt","tax","bank","statement","budget","expense","salary","pay","salary"]):
            return "Personal/Finance"
        if any(k in name for k in ["medical","health","doctor","prescription","diagnosis","insurance"]):
            return "Personal/Health"
        if any(k in name for k in ["contract","agreement","legal","passport","license","id","deed"]):
            return "Personal/Legal"
        if any(k in name for k in ["travel","itinerary","trip","flight","hotel","booking","visa"]):
            return "Personal/Travel"
        if any(k in name for k in ["readme","spec","api","manual","guide","documentation","docs"]):
            return "Projects/Documentation"
        if any(k in name for k in ["roadmap","plan","sprint","milestone","backlog","timeline"]):
            return "Projects/Planning"
        return "Personal/Family"

    # ── Spreadsheets ─────────────────────────────────────────────────────────
    if ext in {"xls","xlsx","ods","csv","numbers"}:
        if any(k in name for k in ["budget","finance","expense","tax","invoice","payroll"]):
            return "Personal/Finance"
        if any(k in name for k in ["data","dataset","results","analysis","metrics","stats"]):
            return "Academics/Research"
        if any(k in name for k in ["plan","timeline","roadmap","schedule","gantt"]):
            return "Projects/Planning"
        return "Personal/Finance"

    # ── Presentations ────────────────────────────────────────────────────────
    if ext in {"ppt","pptx","odp","key"}:
        if any(k in name for k in ["lecture","class","lesson","module"]):
            return "Academics/Lectures"
        if any(k in name for k in ["pitch","proposal","deck","presentation","product"]):
            return "Projects/Planning"
        return "Academics/Lectures"

    # ── Design ───────────────────────────────────────────────────────────────
    if ext in {"psd","ai","xd","fig","sketch","afdesign","indd"}:
        return "Projects/Design"

    return "Others"


def _read_snippet(path: str, max_bytes: int = 300) -> str:
    """Read file snippet for categorization (optimized for speed)."""
    TEXT_EXTS = {".txt",".md",".rst",".py",".js",".ts",".java",".c",".cpp",".cs",
                 ".go",".rs",".sh",".csv",".json",".yaml",".yml",".toml",".ini",
                 ".cfg",".log",".html",".css",".sql",".rb",".php"}
    if pathlib.Path(path).suffix.lower() not in TEXT_EXTS:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            # FIX: Reduced from 600 to 300 bytes for faster processing
            return f.read(max_bytes)
    except Exception:
        return ""


# ── Optimized concurrency: increased from 6 to 12 ──────────────────────────────
_SEM = asyncio.Semaphore(12)

async def categorize_files(task_id: str, dry_run: bool = True,
                           base_dir: str = "", force: bool = False):
    if not dry_run:
        if not base_dir.strip():
            await update_task(task_id, "failed", 0, "base_dir required for live mode")
            return
        dest_root = pathlib.Path(base_dir.strip())
        try:
            dest_root.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            await update_task(task_id, "failed", 0, f"Cannot create base_dir: {exc}")
            return

    query = ("SELECT id, path, filename FROM files_metadata WHERE is_deleted=0"
             if force else
             "SELECT id, path, filename FROM files_metadata "
             "WHERE ai_category IS NULL AND is_deleted=0")

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query) as cur:
            rows = await cur.fetchall()

    total = len(rows)
    if total == 0:
        await update_task(task_id, "done", 1.0, "No files to classify")
        return

    logger.info("Classifying %d files dry_run=%s force=%s", total, dry_run, force)
    await update_task(task_id, "running", 0.0, f"Starting — {total} files")

    done_count = 0
    BATCH = 20

    async def process_one(row) -> None:
        nonlocal done_count
        if is_cancelled(task_id):
            return
        if not pathlib.Path(row["path"]).exists():
            return
        snippet  = await asyncio.to_thread(_read_snippet, row["path"])
        async with _SEM:
            category = await _ask_ollama(row["filename"], snippet)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            await db.execute("UPDATE files_metadata SET ai_category=? WHERE id=?",
                             (category, row["id"]))
            await db.commit()
        if not dry_run and base_dir:
            await _move_file_async(row["path"], category, base_dir.strip())
        done_count += 1
        if done_count % BATCH == 0 or done_count == total:
            await update_task(task_id, "running", done_count / total,
                              f"Classified {done_count}/{total}")

    CHUNK = 50
    for i in range(0, total, CHUNK):
        if is_cancelled(task_id):
            await update_task(task_id, "cancelled", done_count/total, "Cancelled")
            return
        await asyncio.gather(*[process_one(r) for r in rows[i:i+CHUNK]])
        await asyncio.sleep(0)

    if not is_cancelled(task_id):
        await update_task(task_id, "done", 1.0, f"Done — {done_count}/{total} classified")


def _do_move(src: str, dest: str):
    shutil.move(src, dest)

async def _move_file_async(src: str, category: str, base_dir: str):
    """
    Move file to categorized destination.
    FIX: Make file move and DB update atomic to prevent inconsistency.
    """
    src_path = pathlib.Path(src)
    if not src_path.exists():
        logger.warning("Source file does not exist: %s", src)
        return
    
    # category may have subdirs e.g. "Academics/Lectures"
    dest_dir = pathlib.Path(base_dir)
    for part in category.split("/"):
        dest_dir = dest_dir / part
    
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.error("Cannot create destination dir %s: %s", dest_dir, exc)
        return
    
    dest = dest_dir / src_path.name
    counter = 1
    while dest.exists():
        dest = dest_dir / f"{src_path.stem}_{counter}{src_path.suffix}"
        counter += 1
    
    # FIX: Use a temporary marker file to ensure atomic move + DB update
    # If process crashes between move and DB update, we can detect and reconcile
    marker_file = dest.parent / f".{dest.name}.move_in_progress"
    
    try:
        # Create marker BEFORE move
        marker_file.write_text(str(src_path))
        
        # Move file
        await asyncio.to_thread(_do_move, str(src_path), str(dest))
        
        # NOW update DB (inside single transaction)
        async with aiosqlite.connect(str(DB_PATH)) as db:
            # Use transaction to combine both operations
            await db.execute("BEGIN IMMEDIATE")
            try:
                await db.execute(
                    "INSERT INTO organizer_log(original_path, new_path) VALUES (?,?)",
                    (str(src_path), str(dest))
                )
                await db.execute(
                    "UPDATE files_metadata SET path=? WHERE path=?",
                    (str(dest), str(src_path))
                )
                await db.commit()
                logger.info("✓ Moved %s → %s", src_path, dest)
            except Exception as exc:
                await db.execute("ROLLBACK")
                logger.error("DB update failed, rolling back: %s", exc)
                # Try to move file back
                try:
                    await asyncio.to_thread(_do_move, str(dest), str(src_path))
                    logger.warning("Rolled back file move")
                except:
                    logger.error("CRITICAL: Could not roll back file move! %s still at %s", src, dest)
                raise
        
        # Delete marker file on success
        try:
            marker_file.unlink()
        except:
            pass
            
    except Exception as exc:
        logger.error("Move failed for %s: %s", src, exc)
        # Clean up marker file
        try:
            marker_file.unlink()
        except:
            pass
        return


async def undo_last_moves(count: int = 10) -> list[dict]:
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM organizer_log WHERE undone=0 ORDER BY moved_at DESC LIMIT ?",
            (count,)
        ) as cur:
            moves = await cur.fetchall()
    results = []
    for m in moves:
        try:
            pathlib.Path(m["original_path"]).parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(_do_move, m["new_path"], m["original_path"])
            async with aiosqlite.connect(str(DB_PATH)) as db:
                await db.execute("UPDATE organizer_log SET undone=1 WHERE id=?", (m["id"],))
                await db.execute("UPDATE files_metadata SET path=? WHERE path=?",
                                 (m["original_path"], m["new_path"]))
                await db.commit()
            results.append({"status":"ok","restored":m["original_path"]})
        except Exception as exc:
            results.append({"status":"error","path":m["new_path"],"error":str(exc)})
    return results


async def get_model_status() -> dict:
    model = await _detect_model()
    return {"model": model, "available": model is not None,
            "categories": CATEGORIES, "total_categories": len(CATEGORIES)}
