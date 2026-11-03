# Changelog

Changes are recorded here as the project moves toward its first public release.

## Unreleased

- Added an engineering audit and architecture, security, and contributor documentation.
- Improved filesystem path validation to reject linked ancestor components and made recycle-bin names collision resistant.
- Made interrupted background tasks visible as failed after restart, added retry support for existing task types, and preserved handler-reported terminal states.
- Added focused regression tests for path guard and task recovery behavior, plus a backend CI workflow.
- Fixed the missing embedding-dimension probe that prevented indexing/search from starting.
- Added overlapping passage indexing, generation-manifest persistence, source excerpts, match reasons, and a SQLite keyword fallback for Ollama outages.
- Added root-scoped scan/organizer checks, symlink/junction skipping in scans and hashes, escaped search/task UI content, bounded API inputs, and explicit desktop open actions.
- Added a versioned migration registry and a read-only Settings view with real configuration and privacy details.
- Added scan-generation migration and safe missing-file reconciliation after complete scans; incomplete walks do not mark unseen files deleted.
- Corrected duplicate summary totals to derive from active file rows rather than stale hash-index counters.
- Expanded ignore rules for local virtual environments, Python caches, logs, and environment files.
- Preserved the existing ignored SQLite database, WAL files, and vector index artifacts.
