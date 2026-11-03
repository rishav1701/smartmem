# Changelog

Changes are recorded here as the project moves toward its first public release.

## Unreleased

- Added an engineering audit and architecture, security, and contributor documentation.
- Improved filesystem path validation to reject linked ancestor components and made recycle-bin names collision resistant.
- Made interrupted background tasks visible as failed after restart, added retry support for existing task types, and preserved handler-reported terminal states.
- Added focused regression tests for path guard and task recovery behavior, plus a backend CI workflow.
- Expanded ignore rules for local virtual environments, Python caches, logs, and environment files.
- Preserved the existing ignored SQLite database, WAL files, and vector index artifacts.
