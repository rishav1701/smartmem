# Memora architecture (current foundation)

Memora retains SmartMem's local-process architecture while evolving the prototype in small, testable steps. The current implementation is a browser UI served by FastAPI, a local SQLite database, FAISS files on disk, and optional local Ollama inference.

```mermaid
flowchart LR
  UI[Browser UI<br/>frontend/index.html] -->|HTTP JSON| API[FastAPI routers]
  API --> S[Services<br/>scan, search, organizer, analytics]
  API --> Q[In-process async task queue]
  Q --> S
  S --> DB[(SQLite metadata/tasks)]
  S --> V[(FAISS index + ID map)]
  S --> FS[User-selected local directories]
  S -->|Local HTTP only| O[Ollama]
```

## Boundaries

- Routers validate transport inputs and call services.
- Services own filesystem, retrieval, analytics, and model operations.
- SQLite stores selected roots, file metadata, cached extracted text, task records, and organizer history.
- Completed scans track a scan generation and only mark unseen paths deleted if the whole walk/stat pass succeeds.
- FAISS stores passage vectors. A generation manifest atomically selects immutable index/map files; JSON mappings link passages to file metadata IDs, source timestamps, and excerpts. Legacy index/pickle files remain readable for compatibility and are not overwritten.
- Ollama is optional and local by default. File listing, scanning, hashing, and storage analysis do not inherently require it.

## Current design constraints

The queue is process-local; interrupted tasks become failed and can be retried, but execution is not automatically resumed. Passage vectors are generation-persisted, while SQLite metadata commit follows the index manifest; a crash can cause safe duplicate passages on retry. This is recoverable but not a cross-resource transaction.

## Target evolution

```mermaid
flowchart TB
  UI[Typed desktop-oriented UI] --> API[FastAPI API]
  API --> APP[Application services]
  APP --> JOBS[Persisted task state + bounded workers]
  APP --> DB[(SQLite + versioned migrations + FTS5)]
  APP --> IDX[Rebuildable vector index<br/>chunk IDs and model version]
  APP --> EX[Extractor adapters<br/>text, office, PDF, optional OCR]
  EX --> ROOTS[Explicitly approved roots]
  APP --> LOCAL[Ollama local inference]
```

SQLite remains appropriate for a single-user local app. FTS5 is a low-complexity lexical layer that can complement dense retrieval. A separate vector database or distributed task broker would add operational cost without a demonstrated local-user need. A Tauri wrapper can be evaluated after browser workflows and filesystem permission boundaries are stable; no Tauri project currently exists.
