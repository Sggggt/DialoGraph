from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import SearchFilters, SearchRequest


def test_top_k_validation():
    assert SearchRequest(query="centrality").top_k == 6
    with pytest.raises(ValidationError):
        SearchRequest(query="centrality", top_k=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="centrality", top_k=51)


def test_lexical_search_filters(db_session, sample_course, indexed_chunks):
    from app.services.retrieval import lexical_search_chunks

    results = lexical_search_chunks(db_session, sample_course.id, "degree centrality", SearchFilters(chapter="L3"), 5)
    assert results
    assert results[0]["chunk_id"] == indexed_chunks[1][0].id
    assert results[0]["citations"][0]["document_title"] == "Centrality Notes"

    none = lexical_search_chunks(db_session, sample_course.id, "degree centrality", SearchFilters(chapter="L9"), 5)
    assert none == []


def test_force_reparse_batch_marks_existing_files_pending(db_session, sample_course):
    from app.core.config import get_settings
    from app.models import Chunk, Document, DocumentVersion
    from app.services.ingestion import create_uploaded_files_batch
    from app.services.retrieval import list_course_files

    storage_root = get_settings().course_paths_for_name(sample_course.name)["storage_root"]
    source_path = storage_root / "note.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text("# Note\n\nAlready parsed", encoding="utf-8")

    document = Document(
        course_id=sample_course.id,
        title="note",
        source_path=str(source_path),
        source_type="markdown",
        tags=["Unit"],
        checksum="checksum",
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version=1,
        checksum="checksum",
        storage_path=str(source_path),
        extracted_path=None,
        is_active=True,
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(
        Chunk(
            course_id=sample_course.id,
            document_id=document.id,
            document_version_id=version.id,
            content="Already parsed",
            snippet="Already parsed",
            chapter="Unit",
            section="Note",
            source_type="markdown",
            metadata_json={"content_kind": "markdown"},
            embedding_status="ready",
        )
    )
    db_session.commit()

    before = next(item for item in list_course_files(db_session, sample_course.id) if item["source_path"] == str(source_path))
    assert before["status"] == "parsed"

    create_uploaded_files_batch(db_session, sample_course.id, [source_path], force=True)

    after = next(item for item in list_course_files(db_session, sample_course.id) if item["source_path"] == str(source_path))
    assert after["status"] == "pending"
    assert after["job_state"] == "queued"


@pytest.mark.asyncio
async def test_hybrid_search_rrf_fusion(db_session, sample_course, indexed_chunks, monkeypatch):
    from app.services import retrieval
    from app.services.retrieval import hybrid_search_chunks

    _, chunks = indexed_chunks
    dense_payload = {
        "chunk_id": chunks[1].id,
        "snippet": chunks[1].snippet,
        "score": 0.95,
        "citations": [],
        "metadata": {"scores": {"dense": 0.95}},
        "content": chunks[1].content,
        "document_title": "Centrality Notes",
        "source_path": "centrality.md",
        "chapter": "L3",
        "source_type": "markdown",
    }

    async def fake_dense_search(db, course_id, query, filters, top_k):
        return [dense_payload]

    monkeypatch.setattr(retrieval, "dense_search_chunks", fake_dense_search)

    results = await hybrid_search_chunks(db_session, sample_course.id, "degree centrality", SearchFilters(), 2)
    result_ids = {item["chunk_id"] for item in results}
    assert chunks[0].id in result_ids
    assert chunks[1].id in result_ids
    assert any("fused" in item["metadata"]["scores"] for item in results)
