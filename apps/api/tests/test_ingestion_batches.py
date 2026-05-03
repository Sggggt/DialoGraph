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
