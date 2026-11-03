# Memora

> Your digital life, remembered intelligently.

Memora is the next name for SmartMem, a local-first file intelligence prototype. It scans user-selected folders, tracks file metadata, detects exact duplicates, offers storage views and AI-assisted categorization, and can index document text for local semantic search with Ollama and FAISS.

**Status:** early development. The current application is a FastAPI backend and a single-file HTML/CSS/JavaScript frontend. It is not yet a production release: search currently embeds one truncated representation per file, the task queue does not recover work after restart, and no automated test suite or desktop package is present. See [PROJECT_AUDIT.md](PROJECT_AUDIT.md) for verified findings and priorities.

## Current capabilities

- Add and scan explicitly selected local directories.
- Track file metadata and compute SHA-256 hashes for duplicate grouping.
- View storage, file categories, large files, and duplicate groups.
- Run AI-assisted categorization in dry-run mode by default; live moves have an undo log.
- Extract text from several common document, spreadsheet, presentation, text, and code formats.
- Build a FAISS index and combine dense similarity with a keyword overlap score.
- Track background task status and move requested deletions to a recycle directory.

These capabilities have not all received automated verification. OCR, audio transcription, chunked retrieval, cross-document answers with citations, smart collections, settings UI, and Tauri packaging are not currently implemented.

## Architecture

```mermaid
flowchart LR
  Browser[Browser UI] -->|Local HTTP JSON| API[FastAPI]
  API --> Services[Scanning · Search · Organizer · Analytics]
  Services --> SQLite[(SQLite)]
  Services --> FAISS[(FAISS + ID map)]
  Services --> Files[User-selected folders]
  Services -->|Local endpoint| Ollama[Ollama]
  API --> Queue[In-process task queue]
  Queue --> Services
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for current boundaries and the target direction.

## Technology

- Python 3.11+, FastAPI, Pydantic, aiosqlite
- SQLite and FAISS CPU
- Ollama for local model inference (optional for basic file workflows)
- Vanilla JavaScript, HTML, CSS, Chart.js, and D3 for the current frontend

The UI currently loads fonts and chart libraries from third-party CDNs. This conflicts with a strict offline/privacy claim; do not describe the present app as fully offline until these assets are bundled or removed.

## Development setup

Create a Python virtual environment and install the backend development requirements:

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

On macOS/Linux, use `python3 -m venv .venv`, activate `source .venv/bin/activate`, then run the same pip and uvicorn commands. Open `http://127.0.0.1:8000`; API docs are at `/docs`.

### Optional local AI

Install Ollama and pull models supported by the installed application, for example:

```sh
ollama pull llama3.2
ollama pull nomic-embed-text
```

Ollama defaults to `http://127.0.0.1:11434`. Basic metadata scanning, hashes, and storage analysis do not require it. The current settings are environment variables in `backend/config.py`; there is not yet an `.env` loader or user-facing settings page.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SMARTMEM_DB_PATH` | `backend/data/smartmem.db` | SQLite database path |
| `SMARTMEM_RECYCLE_BIN` | `~/.smartmem_recycle` | Destination for requested deletions |
| `OLLAMA_BASE` | `http://127.0.0.1:11434` | Local Ollama endpoint |
| `OLLAMA_TIMEOUT` | `60` | Model request timeout in seconds |
| `ALLOW_ALL_ORIGINS` | `false` | Enable wildcard CORS (avoid for local deployment) |
| `ALLOWED_ORIGINS` | localhost list | CORS origin allowlist |
| `LOG_LEVEL` | `INFO` | Python logging level |

## Use

1. Add a directory in the Directories view.
2. Scan it and monitor the task queue.
3. Optionally compute hashes to populate duplicate groups.
4. Build the document index for semantic search (Ollama and FAISS required).
5. Review organizer suggestions in dry-run mode before enabling file moves.

Do not expose the API to an untrusted network. Review file operations before execution and retain independent backups.

## API

FastAPI's generated OpenAPI UI at `/docs` is the current authoritative API reference. Routes include directory selection, scanning, storage, duplicates, large files, organizer, semantic search, Ollama status, and task monitoring. Contracts may change while the audit priorities are addressed.

## Privacy and security

Read [SECURITY.md](SECURITY.md). The app stores paths, file metadata, optional extracted text, hashes, task state, categorization, and derived vectors locally. AI requests are intended for the local Ollama endpoint. Current CDN dependencies mean the frontend can contact external hosts for static assets. Data is not encrypted by the app. Selected-root checks are not authentication; bind to loopback.

## Quality and contribution

Focused tests and a GitHub Actions pytest workflow now cover path guard behavior and interrupted task recovery. The wider feature suite remains incomplete, and the new tests could not be run in the current environment because its Python interpreter is missing. See [CONTRIBUTING.md](CONTRIBUTING.md) for development expectations and [CHANGELOG.md](CHANGELOG.md) for changes. A license has not been selected; the owner should choose one after confirming rights to all included work.

## Roadmap

The engineering priorities are documented in [PROJECT_AUDIT.md](PROJECT_AUDIT.md): add characterization tests and CI; repair task lifecycle and recovery; version database migrations; make vector state rebuildable and consistent; add chunk-level retrieval with source provenance; then improve frontend component boundaries, privacy/offline behavior, and desktop packaging.
