# Contributing

Memora is evolving from a local-first prototype. Keep changes focused, preserve existing user data, and describe platform or model assumptions.

## Development

1. Use Python 3.11 or newer.
2. Create a virtual environment in `backend/.venv` and install `backend/requirements.txt`.
3. Run the API from `backend` with `uvicorn main:app --host 127.0.0.1 --port 8000`.
4. Run the automated suite with `python -m pytest`.

Ollama is optional for basic file workflows. For AI workflows, install Ollama locally and pull a supported chat and embedding model. Do not use real personal directories in tests; use isolated temporary fixtures and mock local model calls.

## Change expectations

- Explain user-visible and data-model changes.
- Keep filesystem paths within explicit selected roots and avoid logging file contents.
- Never move/delete files or rebuild user indexes implicitly.
- Add regression coverage for changed behavior.
- Do not claim tests passed unless they were run.
- Do not add generated databases, indexes, caches, virtual environments, or secrets to Git.

## Licensing

No license is currently included. The project owner must select a license after confirming they have the right to license all included material.
