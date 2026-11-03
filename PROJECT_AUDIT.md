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

1. **Automated quality gates are only a start.** Focused path/task, scanner, migration, search, and duplicate tests plus a pytest GitHub Actions workflow now exist. API, extraction, organizer, and end-to-end coverage is still missing, as are lint/format configuration, frontend type checking, and a dependency lock. The tests could not be executed in this environment.
2. **Task recovery is partial.** The task queue remains in-process, so restart cannot resume interrupted work. This pass marks queued/running rows failed with a retry explanation and adds a retry endpoint for supported task types. Work is not silently lost, but automatic resumption and idempotency guarantees are not provided. Handler terminal states are no longer overwritten by the worker.
3. **Vector consistency is improved but not transactional across SQLite and FAISS.** New generations use immutable index/map files selected by an atomic JSON manifest, and DB vector IDs update after the manifest. A crash between the two systems can cause repeat indexing, but existing generations stay available. Legacy pickle maps remain readable for backward compatibility and must be treated as trusted local data.
4. **Search now embeds overlapping passages and has a local lexical fallback.** Excerpts and computed match reasons are returned. Page/section provenance and grounded conversational answers remain unimplemented. Extraction is bounded by a configurable character limit.
5. **Organizer boundaries are stronger but operation recovery is incomplete.** Live mode remains an explicit user choice. Source/destination paths are rechecked against selected roots and category names are allowlisted. A process crash between file movement and DB history update can still require manual recovery; the marker is not automatically reconciled.

### Important reliability and security issues

- Prior to this audit, path validation checked only the leaf for symlink status; a symlinked parent could escape the selected root. The guard now inspects each existing path component and rejects symlinks/junctions before resolving. Platform-specific behavior still needs tests.
- Recycle directory creation is deferred until a delete action. Recycle-bin moves may cross filesystems; cross-volume rollback remains untested.
- Scanning and hashing skip symlinks/junctions; path-level race behavior remains platform dependent. Successful complete scans now reconcile missing paths within the selected root. If walking or stat calls fail, reconciliation is skipped to avoid false deletion. Scanning still pre-counts separately.
- Hash jobs use a process-global semaphore. The hasher now avoids symlink files and verifies the opened file identity, but does not re-stat after reading to detect concurrent content changes.
- Extraction catches errors and returns empty text, so unsupported, corrupted, and scanned-image files are indistinguishable in UI/state. DOC/PPT/XLS legacy formats, OCR, and transcription are not implemented.
- Settings are environment-only; the Settings view reports effective paths/models/resource bounds, but does not yet edit them. Backup/restore and theme controls are absent.
- CORS now limits methods/headers and disables credentials by default. An opt-in wildcard origin setting remains. There is no authentication; deployment should remain loopback-only.
- Error/log paths may include local filenames/paths. The logging policy needs a deliberate privacy review.
- Primary search, task, duplicate, large-file, organizer-log, and categorised-file inputs are bounded; remaining APIs still need a systematic schema review.
- Several README statements are aspirational or stale (incremental search/indexing and future features are mixed with implemented behavior); screenshots and release instructions are absent.

### Product and architecture gaps

- Frontend is a large single HTML file with global state and imperative DOM rendering; no component boundaries, TypeScript, package manifest, or responsive/browser test coverage.
- No Tauri integration exists. Keeping the browser-based local app working is the least disruptive foundation; a Tauri shell is a separate packaging decision.
- SQLite schema evolution now has an ordered migration registry for legacy `doc_text` and scan-generation tracking; broader migration coverage and backup/restore tools remain absent.
- Storage analytics are filesystem/database summaries, not historical trends. The UI's sunburst is not a navigable, tested hierarchy explorer.
- Duplicate grouping is exact hash based, but no test validates same-size filtering, hash races, metadata comparison, or savings calculations.
- No license is present. An ownership/licensing choice must be made by the project owner before publication.

## Verification performed

- Enumerated source/config/docs while excluding the local virtual environment from the source inventory.
- Read route handlers, DB schema, scanner, extractor/search, task queue, security guard, organizer, storage routes, and frontend API/navigation code.
- Added isolated regression tests for linked path rejection, recycle-bin deletion, task recovery, migration, scan reconciliation, search fallback/chunking, and duplicate totals; configured CI to run pytest. These tests were not executed locally because no working Python interpreter is installed.
- Parsed the frontend's inline JavaScript with Node's `--check` and ran `git diff --check` successfully.
- Confirmed an ignored SQLite DB with WAL sidecars exists and left it untouched.
- Attempted to use both system Python and the project-local `.venv`; the venv's base interpreter path points to a missing Windows Store Python installation, so backend tests and startup could not run here.

## Recommended sequence

1. Restore a working Python 3.11 environment and run the new tests/CI locally; add API, organizer, and full workflow tests.
2. Add automatic crash reconciliation for organizer moves and exercise incomplete-scan behavior on supported platforms.
3. Extend migrations and add explicit database backup/restore.
4. Add page/section provenance and grounded multi-turn question answering after evaluating citation correctness.
5. Make extraction limits/status explicit and add optional format adapters (OCR/transcription only behind local dependencies and settings).
6. Split the frontend into a typed component app and bundle/remove external CDN assets.
7. Add frontend/browser tests and release screenshots after real end-to-end workflows are verified.

This audit is a code review, not a claim that every flow has passed runtime testing.
