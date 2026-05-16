from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_uploaded_parse_marks_batch_extracting_graph_before_rebuild(db_session, sample_course, monkeypatch):
    from app.models import IngestionBatch
    from app.services import ingestion
    from app.services.ingestion import create_uploaded_files_batch, run_uploaded_files_ingestion

    from app.core.config import get_settings

    storage_root = get_settings().course_paths_for_name(sample_course.name)["storage_root"]
    source_path = storage_root / "note.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Note\n\nFresh content", encoding="utf-8")

    batch = create_uploaded_files_batch(db_session, sample_course.id, [source_path], force=False)
    observed_statuses: list[str] = []

    async def fake_ingest_file(*args, **kwargs):
        return {"status": "completed", "source_type": "markdown", "stats": {}}

    async def fake_rebuild_course_graph(session, course_id):
        current = session.get(IngestionBatch, batch.id)
        observed_statuses.append(current.status if current else "missing")
        return {
            "graph_rebuilt": True,
            "graph_nodes": 1,
            "graph_edges": 0,
            "concepts": 1,
            "relations": 0,
            "graph_extraction_provider": "unit-test",
        }

    monkeypatch.setattr(ingestion, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(ingestion, "rebuild_course_graph", fake_rebuild_course_graph)

    result = await run_uploaded_files_ingestion(batch.id, [str(source_path)], force=False)

    assert observed_statuses == ["extracting_graph"]
    assert result["state"] == "completed"


@pytest.mark.asyncio
async def test_uploaded_parse_marks_graph_failure_terminal(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.models import IngestionBatch
    from app.services import ingestion
    from app.services.ingestion import create_uploaded_files_batch, run_uploaded_files_ingestion

    storage_root = get_settings().course_paths_for_name(sample_course.name)["storage_root"]
    source_path = storage_root / "graph-failure.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Note\n\nFresh content", encoding="utf-8")

    batch = create_uploaded_files_batch(db_session, sample_course.id, [source_path], force=False)

    async def fake_ingest_file(*args, **kwargs):
        return {"status": "completed", "source_type": "markdown", "stats": {}}

    async def fake_rebuild_course_graph(session, course_id):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(ingestion, "ingest_file", fake_ingest_file)
    monkeypatch.setattr(ingestion, "rebuild_course_graph", fake_rebuild_course_graph)

    result = await run_uploaded_files_ingestion(batch.id, [str(source_path)], force=False)
    db_session.expire_all()
    refreshed = db_session.get(IngestionBatch, batch.id)

    assert result["state"] == "partial_failed"
    assert refreshed.status == "partial_failed"
    assert refreshed.completed_at is not None
    assert "图谱生成失败" in refreshed.last_error


@pytest.mark.asyncio
async def test_ingest_short_markdown_keeps_parent_and_child_chunks(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.models import Chunk
    from app.services import ingestion
    from app.services.embeddings import EmbeddingCallResult
    from app.services.ingestion import ingest_file

    class FakeEmbeddingProvider:
        settings = get_settings()

        async def embed_texts_with_meta(self, texts, text_type="document"):
            return EmbeddingCallResult(
                vectors=[[1.0, 0.0] for _ in texts],
                provider="unit-test",
                external_called=False,
                fallback_reason=None,
            )

    class FakeChatProvider:
        async def classify_json(self, system_prompt, user_prompt, fallback=None):
            return fallback

    class FakeVectorStore:
        def __init__(self, *args, **kwargs):
            self.points = {}

        def upsert(self, points):
            self.points.update({point["id"]: point for point in points})

        def get_points(self, ids):
            return [self.points[item] for item in ids if item in self.points]

        def health_check(self, course_id, active_chunk_ids):
            return {"ok": True, "missing": [], "stale": []}

        def delete(self, ids):
            for item in ids:
                self.points.pop(item, None)

        async def async_upsert(self, points):
            self.upsert(points)

        async def async_delete(self, ids):
            self.delete(ids)

    monkeypatch.setattr(ingestion, "EmbeddingProvider", FakeEmbeddingProvider)
    monkeypatch.setattr(ingestion, "ChatProvider", FakeChatProvider)
    monkeypatch.setattr(ingestion, "VectorStore", FakeVectorStore)

    storage_root = get_settings().course_paths_for_name(sample_course.name)["storage_root"]
    source_path = storage_root / "short-centrality.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "# Centrality smoke test\n\n"
        "Degree centrality counts incident edges in a graph. "
        "It is a local network-analysis measure used to compare node prominence.",
        encoding="utf-8",
    )

    result = await ingest_file(db_session, source_path, course_id=sample_course.id, rebuild_graph=False, force=True)

    chunks = db_session.query(Chunk).filter(Chunk.document_id == result["document_id"], Chunk.is_active.is_(True)).all()
    assert any((chunk.metadata_json or {}).get("is_parent") for chunk in chunks)
    assert any(not (chunk.metadata_json or {}).get("is_parent") for chunk in chunks)
    assert result["stats"]["chunks"] >= 2
