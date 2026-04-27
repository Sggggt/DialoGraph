from __future__ import annotations


def test_graph_runtime_settings_update_env_and_cache(no_fallback_env, monkeypatch, tmp_path):
    import app.services.runtime_settings as runtime_settings
    from app.core.config import get_settings

    env_path = tmp_path / ".env"
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setenv("GRAPH_EXTRACTION_CHUNK_LIMIT", "120")
    monkeypatch.setenv("GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT", "3")

    payload = runtime_settings.update_model_settings(
        {
            "graph_extraction_chunk_limit": 120,
            "graph_extraction_chunks_per_document": 3,
        }
    )

    text = env_path.read_text(encoding="utf-8")
    assert "GRAPH_EXTRACTION_CHUNK_LIMIT=120" in text
    assert "GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT=3" in text
    assert payload["graph_extraction_chunk_limit"] == 120
    assert payload["graph_extraction_chunks_per_document"] == 3
    assert get_settings().graph_extraction_chunk_limit == 120
    assert get_settings().graph_extraction_chunks_per_document == 3
