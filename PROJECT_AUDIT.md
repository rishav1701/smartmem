# SmartMem engineering audit

**Audit date:** 2026-11-03  
**Scope:** Tracked source, dependency/configuration files, and runtime persistence paths. The ignored SQLite database and its WAL sidecars and existing FAISS files were preserved and not opened for modification.

## Executive summary

SmartMem is a runnable-shaped local FastAPI application with an extensive single-page vanilla frontend, SQLite metadata, FAISS document search, Ollama integration, directory scanning, hashing, categorization, and storage summaries. It is a useful portfolio prototype, but the implementation and its README currently overstate production readiness. There is no test suite, frontend build/test pipeline, migration framework, CI workflow, release documentation, or desktop packaging. Several operations and indexes are not crash-safe or fully incremental.

## Repository map

- `backend/main.py`: FastAPI lifespan, CORS, route registration, health/system metrics, static UI hosting.
- `backend/config.py`: environment-derived paths, Ollama, CORS, flags, and worker settings.
- `backend/database/db.py`: SQLite schema initialization and an ad hoc `doc_text` migration.
- `backend/routers/`: directories, scan, storage, duplicates, large files, organizer, search, tasks.
- `backend/services/`: scanner, duplicate hashing queries, organizer, extraction/search, storage analytics.
- `backend/workers/task_queue.py`: in-process `asyncio.Queue` and SQLite task records.
- `backend/security/guard.py`: directory validation, selected-root checks, recycle-bin move.
- `frontend/index.html`: all markup, styling, navigation, visualizations, and API calls in one file.
- `backend/data/`: FAISS index and mapping artifacts (kept intact); SQLite runtime DB is ignored by Git.

## Verified strengths

- AI calls are directed to a configurable local Ollama endpoint; no cloud AI SDK is present in dependencies.
- SQLite uses parameter binding for most user values and WAL mode.
- File hashing streams data in chunks and duplicate candidates are represented by SHA-256.
- Deletion endpoint checks selected roots and moves files into a recycle directory instead of unlinking them.
- Organizer defaults to dry-run mode; movement has an undo log.
- Search combines FAISS similarity with a lexical overlap score and stores extracted text for indexing.
- The frontend exposes actual API-backed workflows rather than only static dashboard metrics.

## Findings and risks

### Critical before public release

1. **Automated quality gates are only a start.** This pass adds focused path/task recovery tests and a pytest GitHub Actions workflow. API, scan, extraction, duplicate, search, organizer, and end-to-end coverage is still missing, as are lint/format configuration, frontend type checking, and a dependency lock. The new tests could not be executed in this environment.
2. **Task recovery is partial.** The task queue remains in-process, so restart cannot resume interrupted work. This pass marks queued/running rows failed with a retry explanation and adds a retry endpoint for supported task types. Work is not silently lost, but automatic resumption and idempotency guarantees are not provided. Handler terminal states are no longer overwritten by the worker.
3. **Vector index consistency is fragile.** FAISS and its pickle ID map are written as separate files. Rebuild force clears DB IDs before replacing both files, and changed/deleted records do not remove vectors from the index. A crash or mismatch can make DB and index disagree. Pickle loading is unsafe if index files are attacker-controlled.
4. **Search is one vector per file, not chunked retrieval.** Extraction caps each document at 2,000 characters; page/section provenance is discarded. Search snippets and citations are not returned, and there is no grounded conversational answer route. Query embedding depends on Ollama, so semantic search is unavailable while Ollama is offline.
5. **Organizer live mode needs stronger operation boundaries.** Classification updates category state even in dry-run; a move can proceed after categorization without a per-batch approval/confirmation token. Destination paths derive from model category strings, and cancellation/crash recovery around filesystem and DB updates is incomplete.

### Important reliability and security issues

- Prior to this audit, path validation checked only the leaf for symlink status; a symlinked parent could escape the selected root. The guard now inspects each existing path component and rejects symlinks/junctions before resolving. Platform-specific behavior still needs tests.
- Recycle directory creation occurs at import time, and recycle-bin moves may cross filesystems. Collision naming was improved, but cross-volume and rollback behavior remain untested.
- Scanning follows normal `os.walk` defaults but has no explicit junction policy, does not mark deleted files after a complete scan, and pre-counts the tree separately (extra full traversal and race with changes).
- Hash jobs use a process-global semaphore bound at import time and hash files without checking that they did not change during the read.
- Extraction catches errors and returns empty text, so unsupported, corrupted, and scanned-image files are indistinguishable in UI/state. DOC/PPT/XLS legacy formats, OCR, and transcription are not implemented.
- Settings are environment-only; model names, chunking, indexing path, resource controls, privacy data inventory, backup/restore, and UI settings are absent.
- CORS permits all methods/headers, and an opt-in wildcard origin setting is available. There is no authentication; deployment should remain loopback-only.
- Error/log paths may include local filenames/paths. The logging policy needs a deliberate privacy review.
- API inputs such as top-k, pagination limits, thresholds, and undo count are not consistently bounded.
- Several README statements are aspirational or stale (incremental search/indexing and future features are mixed with implemented behavior); screenshots and release instructions are absent.

### Product and architecture gaps

- Frontend is a large single HTML file with global state and imperative DOM rendering; no component boundaries, TypeScript, package manifest, or responsive/browser test coverage.
- No Tauri integration exists. Keeping the browser-based local app working is the least disruptive foundation; a Tauri shell is a separate packaging decision.
- SQLite schema evolution is a hand-written column add, not versioned migrations. There are no backups or restore tools.
- Storage analytics are filesystem/database summaries, not historical trends. The UI's sunburst is not a navigable, tested hierarchy explorer.
- Duplicate grouping is exact hash based, but no test validates same-size filtering, hash races, metadata comparison, or savings calculations.
- No license is present. An ownership/licensing choice must be made by the project owner before publication.

## Verification performed

- Enumerated source/config/docs while excluding the local virtual environment from the source inventory.
- Read route handlers, DB schema, scanner, extractor/search, task queue, security guard, organizer, storage routes, and frontend API/navigation code.
- Added isolated regression tests for linked path rejection, recycle-bin deletion, and interrupted-task recovery; configured CI to run pytest. These tests were not executed locally because no working Python interpreter is installed.
- Parsed the frontend's inline JavaScript with Node's `--check` and ran `git diff --check` successfully.
- Confirmed an ignored SQLite DB with WAL sidecars exists and left it untouched.
- Attempted to use both system Python and the project-local `.venv`; the venv's base interpreter path points to a missing Windows Store Python installation, so backend tests and startup could not run here.

## Recommended sequence

1. Add characterization tests for path security, scan/hash/duplicate behavior, task states, and existing APIs.
2. Fix task lifecycle/restart recovery and bound inputs; test through FastAPI's test client.
3. Version DB schema changes and provide explicit backup/restore.
4. Replace per-file embeddings with versioned chunks and provenance; use a transactional/rebuildable vector mapping strategy and FTS5 as a lexical candidate source.
5. Make extraction limits/status explicit and add optional format adapters (OCR/transcription only behind local dependencies and settings).
6. Split the frontend into a typed component app only after preserving the currently working API contracts.
7. Add CI, reproducible dependencies, security docs, and release screenshots after real end-to-end workflows are verified.

This audit is a code review, not a claim that every flow has passed runtime testing.
