# Security and privacy

## Data flow

The backend scans only directories explicitly added by the user. Metadata and extracted text are stored in the configured SQLite database; vector data and mapping files are stored under the backend data directory. Local AI requests go to the configured Ollama endpoint. The repository does not include a cloud AI client. The app has no account or authentication layer and should be bound to loopback, not exposed to an untrusted network.

## File operations

File movement and deletion can affect user data. The organizer defaults to dry-run, and delete requests are intended to move files into a recycle directory. Review proposed moves and duplicate groups before executing them. Keep backups of important files. Never expose the API to an untrusted network.

Path checks reject traversal and linked path components before resolving. This is defense in depth, not a substitute for OS permissions; junction and race behavior must be tested on each supported platform. Do not treat extracted document content as trusted instructions.

## Local data inventory

- `directories`: selected roots and aliases.
- `files_metadata`: paths, names, timestamps, sizes, hashes, categories, extracted text, and vector IDs.
- `tasks`: operation status and task payloads.
- `organizer_log`: move history for undo.
- FAISS index/map: derived embedding data linked to metadata IDs.
- Recycle directory: files moved by the delete workflow.

The database and index are not encrypted by the application. Filesystem permissions and full-disk encryption remain the user's responsibility. Logs should not contain document bodies; paths can themselves be sensitive.

## Reporting

For a suspected vulnerability, do not include private document contents in an issue. Report the affected endpoint, platform, and reproducible steps to the project maintainer through the repository's chosen private contact channel once configured.
