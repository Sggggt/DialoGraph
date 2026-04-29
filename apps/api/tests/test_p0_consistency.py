from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _EmbeddingResult:
    def __init__(self, count: int) -> None:
        self.vectors = [[0.1, 0.2, 0.3] for _ in range(count)]
        self.provider = "openai_compatible"
        self.external_called = True
        self.fallback_reason = None


class _FakeEmbedder:
    settings = SimpleNamespace(embedding_model="unit-test-embedding")

    async def embed_texts_with_meta(self, texts, text_type="document"):
        return _EmbeddingResult(len(texts))


def _create_active_document(db_session, course, source_path):
    from app.models import Chunk, Document, DocumentVersion

    document = Document(
        course_id=course.id,
        title=source_path.stem,
        source_path=str(source_path),
        source_type="markdown",
        checksum="old-checksum",
        tags=["Unit"],
        is_active=True,
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version=1,
        checksum="old-checksum",
        storage_path=str(source_path),
        is_active=True,
    )
    db_session.add(version)
    db_session.flush()
    chunk = Chunk(
        course_id=course.id,
        document_id=document.id,
        document_version_id=version.id,
        content="Old active content about degree centrality.",
        snippet="Old active content",
        chapter="Unit",
        section="Old",
        source_type="markdown",
        metadata_json={"content_kind": "markdown"},
        embedding_status="ready",
        is_active=True,
    )
    db_session.add(chunk)
    db_session.commit()
    return document, chunk


@pytest.mark.asyncio
async def test_vector_upsert_failure_keeps_old_chunks_active(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.models import Chunk, IngestionCompensationLog
    from app.services import ingestion

    source_path = get_settings().course_paths_for_name(sample_course.name)["storage_root"] / "note.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "# Note\n\nNew content about graph search, degree centrality, retrieval, and chunk consistency.",
        encoding="utf-8",
    )
    _, old_chunk = _create_active_document(db_session, sample_course, source_path)

    class FailingVectorStore:
        def __init__(self, course_name=None):
            pass

        def upsert(self, points):
            raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(ingestion, "EmbeddingProvider", _FakeEmbedder)
    monkeypatch.setattr(ingestion, "VectorStore", FailingVectorStore)

    with pytest.raises(RuntimeError, match="qdrant unavailable"):
        await ingestion.ingest_file(db_session, source_path, course_id=sample_course.id, rebuild_graph=False, force=True)

    active_chunks = db_session.query(Chunk).filter(Chunk.course_id == sample_course.id, Chunk.is_active.is_(True)).all()
    assert [chunk.id for chunk in active_chunks] == [old_chunk.id]
    logs = db_session.query(IngestionCompensationLog).all()
    assert logs
    assert logs[-1].operation == "upsert"
    assert logs[-1].status == "failed"


@pytest.mark.asyncio
async def test_db_activation_failure_deletes_new_vectors(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.models import Chunk
    from app.services import ingestion

    source_path = get_settings().course_paths_for_name(sample_course.name)["storage_root"] / "activation.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "# Note\n\nActivation failure content about graph search, vector storage, and chunk consistency.",
        encoding="utf-8",
    )
    _, old_chunk = _create_active_document(db_session, sample_course, source_path)

    state = {"after_upsert": False, "failed": False, "deleted": []}

    class TrackingVectorStore:
        def __init__(self, course_name=None):
            pass

        def upsert(self, points):
            state["after_upsert"] = True
            state["upserted"] = [point["id"] for point in points]

        def delete(self, ids):
            state["deleted"].extend(ids)

        def get_points(self, ids):
            return []

    real_commit = db_session.commit

    def maybe_fail_commit():
        if state["after_upsert"] and not state["failed"]:
            state["failed"] = True
            raise RuntimeError("activation failed")
        return real_commit()

    monkeypatch.setattr(ingestion, "EmbeddingProvider", _FakeEmbedder)
    monkeypatch.setattr(ingestion, "VectorStore", TrackingVectorStore)
    monkeypatch.setattr(db_session, "commit", maybe_fail_commit)

    with pytest.raises(RuntimeError, match="activation failed"):
        await ingestion.ingest_file(db_session, source_path, course_id=sample_course.id, rebuild_graph=False, force=True)

    assert state["upserted"]
    assert sorted(state["deleted"]) == sorted(state["upserted"])
    active_chunks = db_session.query(Chunk).filter(Chunk.course_id == sample_course.id, Chunk.is_active.is_(True)).all()
    assert [chunk.id for chunk in active_chunks] == [old_chunk.id]


@pytest.mark.asyncio
async def test_ingest_file_serializes_same_source_path(monkeypatch, tmp_path):
    from app.services import ingestion

    source_path = tmp_path / "same.md"
    source_path.write_text("same", encoding="utf-8")
    state = {"current": 0, "max": 0}

    async def fake_locked(**kwargs):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        await asyncio.sleep(0.01)
        state["current"] -= 1
        return {"status": "completed"}

    monkeypatch.setattr(ingestion, "_ingest_file_locked", fake_locked)
    await asyncio.gather(ingestion.ingest_file(None, source_path), ingestion.ingest_file(None, source_path))

    assert state["max"] == 1


def test_create_uploaded_files_batch_reuses_active_course_batch(db_session, sample_course, tmp_path):
    from app.services.ingestion import create_sync_batch, create_uploaded_files_batch

    active = create_sync_batch(db_session, sample_course.id, tmp_path, trigger_source="storage")
    reused = create_uploaded_files_batch(db_session, sample_course.id, [tmp_path / "note.md"])

    assert reused.id == active.id


def test_pending_upsert_compensation_keeps_active_vectors(db_session, sample_course, tmp_path, monkeypatch):
    from app.models import IngestionCompensationLog
    from app.services import ingestion

    source_path = tmp_path / "active.md"
    source_path.write_text("# Active\n\nAlready active", encoding="utf-8")
    _, active_chunk = _create_active_document(db_session, sample_course, source_path)
    log = IngestionCompensationLog(
        course_id=sample_course.id,
        operation="upsert",
        vector_ids=[active_chunk.id],
        status="pending",
    )
    db_session.add(log)
    db_session.commit()
    deleted: list[str] = []

    class TrackingVectorStore:
        def __init__(self, course_name=None):
            pass

        def delete(self, ids):
            deleted.extend(ids)

    monkeypatch.setattr(ingestion, "VectorStore", TrackingVectorStore)

    assert ingestion.process_pending_vector_compensations(db_session) == 1
    assert deleted == []
    db_session.refresh(log)
    assert log.status == "completed"


@pytest.mark.asyncio
async def test_ingest_uses_enriched_embedding_text_and_payload_marker(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.services import ingestion
    from app.services.chunking import EMBEDDING_TEXT_VERSION

    source_path = get_settings().course_paths_for_name(sample_course.name)["storage_root"] / "enriched.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        "# Centrality\n\nDegree centrality counts incident edges in a graph and supports network analysis.",
        encoding="utf-8",
    )
    captured = {"texts": [], "points": []}

    class CapturingEmbedder(_FakeEmbedder):
        async def embed_texts_with_meta(self, texts, text_type="document"):
            captured["texts"] = texts
            return await super().embed_texts_with_meta(texts, text_type=text_type)

    class CapturingVectorStore:
        def __init__(self, course_name=None):
            pass

        def upsert(self, points):
            captured["points"] = points

    monkeypatch.setattr(ingestion, "EmbeddingProvider", CapturingEmbedder)
    monkeypatch.setattr(ingestion, "VectorStore", CapturingVectorStore)

    result = await ingestion.ingest_file(db_session, source_path, course_id=sample_course.id, rebuild_graph=False)

    assert result["status"] == "completed"
    assert captured["texts"]
    assert captured["texts"][0].startswith("Document: enriched\nChapter: enriched\n")
    assert "Content Kind: markdown" in captured["texts"][0]
    assert "Degree centrality counts incident edges" in captured["texts"][0]
    assert captured["points"][0]["payload"]["embedding_text_version"] == EMBEDDING_TEXT_VERSION
    assert result["stats"]["embedding_text_version"] == EMBEDDING_TEXT_VERSION


@pytest.mark.asyncio
async def test_ingest_skips_duplicate_document_in_same_course(db_session, sample_course, tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.models import Document, DocumentVersion
    from app.services import ingestion
    from app.services.storage import compute_checksum

    storage_root = get_settings().course_paths_for_name(sample_course.name)["storage_root"]
    original = storage_root / "duplicate.md"
    duplicate = storage_root / "copies" / "duplicate.md"
    original.parent.mkdir(parents=True, exist_ok=True)
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    text = "# Duplicate\n\nDegree centrality duplicate document with enough text to be indexed."
    original.write_text(text, encoding="utf-8")
    duplicate.write_text(text, encoding="utf-8")
    checksum = compute_checksum(original)
    existing = Document(
        course_id=sample_course.id,
        title="duplicate",
        source_path=str(original),
        source_type="markdown",
        checksum=checksum,
        tags=["duplicate"],
        is_active=True,
    )
    db_session.add(existing)
    db_session.flush()
    db_session.add(DocumentVersion(document_id=existing.id, version=1, checksum=checksum, storage_path=str(original), is_active=True))
    db_session.commit()

    class UnexpectedEmbedder(_FakeEmbedder):
        async def embed_texts_with_meta(self, texts, text_type="document"):
            raise AssertionError("duplicate document should not be embedded")

    monkeypatch.setattr(ingestion, "EmbeddingProvider", UnexpectedEmbedder)

    result = await ingestion.ingest_file(db_session, duplicate, course_id=sample_course.id, rebuild_graph=False)

    assert result["status"] == "skipped"
    assert result["document_id"] == existing.id
    assert result["stats"]["deduplicated_document"] is True
    assert result["stats"]["embedding_fallback_reason"] == "duplicate_document"


@pytest.mark.asyncio
async def test_ingest_deduplicates_chunks_and_skips_empty_effective_payload(db_session, sample_course, monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.models import Chunk, Document, DocumentVersion
    from app.services import ingestion

    storage_root = get_settings().course_paths_for_name(sample_course.name)["storage_root"]
    source_path = storage_root / "dedup.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Dedup\n\nFresh source", encoding="utf-8")
    document = Document(
        course_id=sample_course.id,
        title="existing",
        source_path=str(tmp_path / "existing.md"),
        source_type="markdown",
        checksum="existing",
        tags=["existing"],
        is_active=True,
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(document_id=document.id, version=1, checksum="existing", storage_path="existing.md", is_active=True)
    db_session.add(version)
    db_session.flush()
    db_session.add(
        Chunk(
            course_id=sample_course.id,
            document_id=document.id,
            document_version_id=version.id,
            content="Degree centrality duplicate chunk with enough text to test content hashing.",
            snippet="Degree centrality duplicate chunk",
            chapter="Existing",
            section="Existing",
            source_type="markdown",
            metadata_json={"content_kind": "markdown"},
            embedding_status="ready",
            is_active=True,
        )
    )
    db_session.commit()

    def fake_chunk_sections(*args, **kwargs):
        return (
            [
                {
                    "content": "Degree centrality duplicate chunk with enough text to test content hashing.",
                    "snippet": "duplicate",
                    "chapter": "Dedup",
                    "section": "Dedup",
                    "page_number": None,
                    "token_count": 9,
                    "metadata": {"content_kind": "markdown"},
                }
            ],
            {"chunks_before_filter": 1, "chunks_filtered": 0},
        )

    class UnexpectedEmbedder(_FakeEmbedder):
        async def embed_texts_with_meta(self, texts, text_type="document"):
            raise AssertionError("fully deduplicated chunks should not be embedded")

    monkeypatch.setattr(ingestion, "chunk_sections_with_stats", fake_chunk_sections)
    monkeypatch.setattr(ingestion, "EmbeddingProvider", UnexpectedEmbedder)

    result = await ingestion.ingest_file(db_session, source_path, course_id=sample_course.id, rebuild_graph=False)

    assert result["status"] == "skipped"
    assert result["stats"]["chunks_deduplicated"] == 1
    assert result["stats"]["embedding_fallback_reason"] == "no_effective_chunks"
