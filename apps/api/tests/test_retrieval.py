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
async def test_hybrid_search_uses_weighted_fusion_and_rerank(db_session, sample_course, indexed_chunks, monkeypatch):
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

    async def fake_dense_search(db, course_id, query, filters, top_k, model_audit=None):
        if model_audit is not None:
            model_audit.update({"embedding_provider": "openai_compatible", "embedding_external_called": True})
        return [dense_payload]

    class FakeReranker:
        def rerank(self, query, candidates, top_k):
            for item in candidates:
                item.setdefault("metadata", {}).setdefault("scores", {})["rerank"] = item["score"]
            return sorted(candidates, key=lambda item: item["score"], reverse=True)[:top_k]

    monkeypatch.setattr(retrieval, "dense_search_chunks", fake_dense_search)
    monkeypatch.setattr(retrieval.RerankerProvider, "get", classmethod(lambda cls: FakeReranker()))

    results = await hybrid_search_chunks(db_session, sample_course.id, "degree centrality", SearchFilters(), 2)
    result_ids = {item["chunk_id"] for item in results}
    assert chunks[0].id in result_ids
    assert chunks[1].id in result_ids
    assert any("fused" in item["metadata"]["scores"] for item in results)
    assert all("rerank" in item["metadata"]["scores"] for item in results)
    assert all("query_type" in item["metadata"]["scores"] for item in results)


@pytest.mark.asyncio
async def test_hybrid_search_can_skip_reranker(db_session, sample_course, indexed_chunks, monkeypatch):
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

    async def fake_dense_search(db, course_id, query, filters, top_k, model_audit=None):
        if model_audit is not None:
            model_audit.update({"embedding_provider": "openai_compatible", "embedding_external_called": True})
        return [dense_payload]

    class FakeSettings:
        embedding_model = "unit-test-embedding"
        enable_model_fallback = False
        retrieval_recall_k_default = 64
        retrieval_recall_k_formula = 80
        reranker_enabled = False

    def fail_if_called():
        raise AssertionError("reranker should not be called when disabled")

    monkeypatch.setattr(retrieval, "dense_search_chunks", fake_dense_search)
    monkeypatch.setattr(retrieval, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(retrieval.RerankerProvider, "get", classmethod(lambda cls: fail_if_called()))

    results = await hybrid_search_chunks(db_session, sample_course.id, "degree centrality", SearchFilters(), 2)
    assert results
    assert all(item["metadata"]["scores"]["rerank_enabled"] is False for item in results)
    assert all(item["metadata"]["scores"]["rerank"] is None for item in results)


@pytest.mark.asyncio
async def test_search_chunks_with_audit_reports_real_query_embedding(db_session, sample_course, indexed_chunks, monkeypatch):
    from app.services import retrieval
    from app.services.embeddings import EmbeddingCallResult

    _, chunks = indexed_chunks

    async def fake_embed(self, texts, text_type="document"):
        assert texts == ["degree centrality"]
        assert text_type == "query"
        return EmbeddingCallResult(vectors=[[0.1, 0.2, 0.3]], provider="openai_compatible", external_called=True)

    class FakeVectorStore:
        def __init__(self, course_name):
            self.course_name = course_name

        def search(self, *, vector, limit, filters):
            assert vector == [0.1, 0.2, 0.3]
            return [{"id": chunks[0].id, "score": 0.9}]

    class FakeReranker:
        def rerank(self, query, candidates, top_k):
            return candidates[:top_k]

    monkeypatch.setattr(retrieval.EmbeddingProvider, "embed_texts_with_meta", fake_embed)
    monkeypatch.setattr(retrieval, "VectorStore", FakeVectorStore)
    monkeypatch.setattr(retrieval.RerankerProvider, "get", classmethod(lambda cls: FakeReranker()))

    results, audit = await retrieval.search_chunks_with_audit(db_session, sample_course.id, "degree centrality", SearchFilters(), 1)

    assert results
    assert audit["embedding_provider"] == "openai_compatible"
    assert audit["embedding_external_called"] is True
    assert audit["embedding_fallback_reason"] is None
    assert results[0]["metadata"]["model_audit"]["embedding_external_called"] is True


@pytest.mark.asyncio
async def test_search_ignores_zero_score_dense_index_and_uses_lexical(db_session, sample_course, indexed_chunks, monkeypatch):
    from app.services import retrieval
    from app.services.embeddings import EmbeddingCallResult

    _, chunks = indexed_chunks

    async def fake_embed(self, texts, text_type="document"):
        return EmbeddingCallResult(vectors=[[0.1, 0.2, 0.3]], provider="openai_compatible", external_called=True)

    class ZeroVectorStore:
        def __init__(self, course_name):
            self.course_name = course_name

        def search(self, *, vector, limit, filters):
            return [{"id": chunk.id, "score": 0.0} for chunk in chunks]

    class PassThroughReranker:
        def rerank(self, query, candidates, top_k):
            return candidates[:top_k]

    monkeypatch.setattr(retrieval.EmbeddingProvider, "embed_texts_with_meta", fake_embed)
    monkeypatch.setattr(retrieval, "VectorStore", ZeroVectorStore)
    monkeypatch.setattr(retrieval.RerankerProvider, "get", classmethod(lambda cls: PassThroughReranker()))

    results, audit = await retrieval.search_chunks_with_audit(db_session, sample_course.id, "degree centrality", SearchFilters(), 2)

    assert results
    assert audit["vector_index_warning"] == "qdrant_returned_only_zero_scores"
    assert all(item["metadata"]["scores"]["dense"] is None for item in results)
    assert any("bm25" in item["metadata"]["scores"] for item in results)


@pytest.mark.asyncio
async def test_search_scores_include_primary_channels_for_dense_only_results(db_session, sample_course, indexed_chunks, monkeypatch):
    from app.services import retrieval
    from app.services.embeddings import EmbeddingCallResult

    _, chunks = indexed_chunks

    async def fake_embed(self, texts, text_type="document"):
        return EmbeddingCallResult(vectors=[[0.1, 0.2, 0.3]], provider="openai_compatible", external_called=True)

    class DenseOnlyStore:
        def __init__(self, course_name):
            self.course_name = course_name

        def search(self, *, vector, limit, filters):
            return [{"id": chunks[0].id, "score": 0.8}]

    class FakeReranker:
        def rerank(self, query, candidates, top_k):
            for item in candidates:
                item.setdefault("metadata", {}).setdefault("scores", {})["rerank"] = 0.5
            return candidates[:top_k]

    monkeypatch.setattr(retrieval.EmbeddingProvider, "embed_texts_with_meta", fake_embed)
    monkeypatch.setattr(retrieval, "VectorStore", DenseOnlyStore)
    monkeypatch.setattr(retrieval, "lexical_search_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(retrieval.RerankerProvider, "get", classmethod(lambda cls: FakeReranker()))

    results, audit = await retrieval.search_chunks_with_audit(db_session, sample_course.id, "no lexical match", SearchFilters(), 1)

    scores = results[0]["metadata"]["scores"]
    assert audit["reranker_called"] is True
    assert scores["dense"] == 0.8
    assert scores["lexical"] is None
    assert scores["fused"] is None
    assert scores["rerank"] == 0.5
