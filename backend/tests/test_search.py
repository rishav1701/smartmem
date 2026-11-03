import aiosqlite
import pytest

from services.search import semantic_search as search


def test_chunk_document_bounds_chunks_and_keeps_overlap():
    text = "x" * 500
    chunks = search._chunk_document(text, size=200, overlap=40)
    assert len(chunks) == 3
    assert all(0 < len(chunk) <= 200 for chunk in chunks)
    assert chunks[0][-40:] == chunks[1][:40]


def test_snippet_is_a_verbatim_source_excerpt():
    source = "First section. Neural networks use layers and weights. Last section."
    snippet = search._make_snippet(source, "neural networks", max_chars=40)
    assert "Neural networks" in snippet
    assert snippet.strip("…") in source


@pytest.mark.asyncio
async def test_search_uses_local_lexical_fallback_when_ollama_is_offline(tmp_path, monkeypatch):
    db_path = tmp_path / "search.sqlite3"
    monkeypatch.setattr(search, "DB_PATH", db_path)
    monkeypatch.setattr(search, "_index", None)
    monkeypatch.setattr(search, "_detect_embed_model", _no_model)
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "CREATE TABLE files_metadata (id INTEGER PRIMARY KEY, path TEXT, "
            "filename TEXT, size_bytes INTEGER, ai_category TEXT, extension TEXT, "
            "modified_at TEXT, accessed_at TEXT, doc_text TEXT, is_deleted INTEGER)"
        )
        await db.execute(
            "INSERT INTO files_metadata(path, filename, size_bytes, extension, "
            "doc_text, is_deleted) VALUES (?, ?, ?, ?, ?, 0)",
            ("/notes/db.pdf", "database.pdf", 42, ".pdf",
             "Normalization forms reduce database redundancy and update anomalies."),
        )
        await db.commit()

    results = await search.semantic_search("normalization redundancy", 5)

    assert len(results) == 1
    assert results[0]["filename"] == "database.pdf"
    assert results[0]["semantic_score"] is None
    assert "normalization" in results[0]["snippet"].lower()


async def _no_model():
    return None


@pytest.mark.asyncio
async def test_detect_dim_probes_and_caches_dimension(monkeypatch):
    monkeypatch.setattr(search, "_embed_dim", None)

    async def fake_embed(model, text):
        assert model == "test-model"
        assert "probe" in text
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(search, "_raw_embed", fake_embed)
    assert await search._detect_dim("test-model") == 3
    assert await search._detect_dim("test-model") == 3
