# Security and privacy

## Data flow

The backend scans only directories explicitly added by the user. Metadata and extracted text are stored in the configured SQLite database; vector data and mapping files are stored under the backend data directory. AI requests go to the configured Ollama endpoint; the repository does not include a cloud AI client. The UI uses browser-native assets and makes no third-party static asset requests. Search paths and snippets render escaped text in the UI. The app has no account or authentication layer. Middleware rejects non-loopback clients and Host headers outside localhost/loopback, in addition to restrictive CORS and browser security headers.

When `OLLAMA_AUTOSTART=true`, startup may launch the installed Ollama executable using a direct argument list (no shell), and only for an HTTP loopback endpoint. A spawned server binds to `127.0.0.1`; Memora does not start Ollama for remote endpoints. The server can remain running after Memora exits, consistent with Ollama's background service behavior. Set `OLLAMA_AUTOSTART=false` to disable automatic startup.

## File operations

File movement and deletion can affect user data. The organizer defaults to dry-run, and delete requests are intended to move files into a recycle directory. Review proposed moves and duplicate groups before executing them. Keep backups of important files. Never expose the API to an untrusted network.

Path checks reject traversal and linked path components before resolving. Scanning and organizer operations re-check selected-root scope; scanner and hasher skip symlinks and junctions. Open actions use argument-array desktop launchers without a command shell and require an explicit click. This is defense in depth, not a substitute for OS permissions; junction and race behavior must be tested on each supported platform. Do not treat extracted document content as trusted instructions.

## Local data inventory

- `directories`: selected roots and aliases.
- `files_metadata`: paths, names, timestamps, sizes, hashes, categories, extracted text, and vector IDs.
- `tasks`: operation status and task payloads.
- `organizer_log`: move history for undo.
- FAISS index/map: derived embedding data linked to metadata IDs.
- Recycle directory: files moved by the delete workflow.

The database and index are not encrypted by the application. Filesystem permissions and full-disk encryption remain the user's responsibility. Logs should not contain document bodies; paths can themselves be sensitive. The CSP currently permits inline scripts/styles because the single-page UI uses inline code; dynamic content must continue to be escaped and this policy is not a substitute for a componentized UI with nonces. Legacy FAISS maps are preserved but never unpickled; the application builds a JSON-backed generation instead.

## Reporting

For a suspected vulnerability, do not include private document contents in an issue. Report the affected endpoint, platform, and reproducible steps to the project maintainer through the repository's chosen private contact channel once configured.
