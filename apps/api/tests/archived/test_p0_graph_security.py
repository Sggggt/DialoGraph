from __future__ import annotations

import pytest

from app.schemas import SearchFilters


@pytest.mark.asyncio
async def test_evidence_first_search_adds_verified_path_evidence_chunk(db_session, sample_course, indexed_chunks, monkeypatch):
    from app.models import Chunk, Concept, ConceptRelation, DocumentVersion
    from app.services import retrieval
    from app.services.retrieval import evidence_first_search_chunks_with_audit

    document, chunks = indexed_chunks
    version = db_session.query(DocumentVersion).filter(DocumentVersion.document_id == document.id).first()
    related_parent = Chunk(
        course_id=sample_course.id,
        document_id=document.id,
        document_version_id=version.id,
        content="Full parent section comparing degree, closeness, and shortest-path centralities.",
        snippet="Full parent section comparing centralities.",
        chapter="L3",
        section="Centrality",
        source_type="markdown",
        metadata_json={"content_kind": "markdown", "is_parent": True},
        embedding_status="ready",
        is_active=True,
    )
    db_session.add(related_parent)
    db_session.flush()
    related_chunk = Chunk(
        course_id=sample_course.id,
        document_id=document.id,
        document_version_id=version.id,
        content="Closeness centrality uses distances to all other nodes.",
        snippet="Closeness centrality uses distances.",
        chapter="L3",
        section="Centrality",
        source_type="markdown",
        metadata_json={"content_kind": "markdown", "is_parent": False},
        parent_chunk_id=related_parent.id,
        embedding_status="ready",
        is_active=True,
    )
    db_session.add(related_chunk)
    source = Concept(
        course_id=sample_course.id,
        canonical_name="Degree Centrality",
        normalized_name="degree centrality",
        summary="Degree",
        importance_score=0.8,
    )
    target = Concept(
        course_id=sample_course.id,
        canonical_name="Closeness Centrality",
        normalized_name="closeness centrality",
        summary="Closeness",
        importance_score=0.7,
    )
    bridge = Concept(
        course_id=sample_course.id,
        canonical_name="Shortest Path Distance",
        normalized_name="shortest path distance",
        summary="Distance",
        importance_score=0.6,
    )
    db_session.add_all([source, target, bridge])
    db_session.flush()
    db_session.add_all(
        [
            ConceptRelation(
                course_id=sample_course.id,
                source_concept_id=source.id,
                target_concept_id=target.id,
                target_name=target.canonical_name,
                relation_type="related_to",
                evidence_chunk_id=chunks[0].id,
                confidence=0.9,
                weight=0.9,
                is_validated=True,
                relation_source="llm",
                metadata_json={"evidence_source_match": True, "evidence_target_match": True},
            ),
            ConceptRelation(
                course_id=sample_course.id,
                source_concept_id=target.id,
                target_concept_id=bridge.id,
                target_name=bridge.canonical_name,
                relation_type="contrasts_with",
                evidence_chunk_id=related_chunk.id,
                confidence=0.8,
                weight=0.8,
                is_validated=True,
                relation_source="llm",
                metadata_json={"evidence_source_match": True, "evidence_target_match": True},
            ),
        ]
    )
    db_session.commit()

    async def fake_hybrid_with_audit(db, course_id, query, filters, top_k):
        return (
            [
                {
                    "chunk_id": chunks[0].id,
                    "snippet": chunks[0].snippet,
                    "score": 1.0,
                    "citations": [],
                    "metadata": {
                        "scores": {"dense": 1.0, "fused": 1.0},
                        "route_eligibility": {"retrieval": True, "graph_extraction": True},
                    },
                    "content": chunks[0].content,
                    "document_title": document.title,
                    "source_path": document.source_path,
                    "chapter": chunks[0].chapter,
                    "source_type": chunks[0].source_type,
                }
            ],
            {"retrieval_pipeline": "base_test"},
        )

    monkeypatch.setattr(retrieval, "hybrid_search_chunks_with_audit", fake_hybrid_with_audit)

    results, audit = await evidence_first_search_chunks_with_audit(
        db_session,
        sample_course.id,
        "compare centrality",
        SearchFilters(),
        2,
        route="multi_hop_research",
    )
    result_ids = {item["chunk_id"] for item in results}

    assert chunks[0].id in result_ids
    assert related_chunk.id in result_ids
    expanded = next(item for item in results if item["chunk_id"] == related_chunk.id)
    assert audit["retrieval_pipeline"] == "evidence_first_v1"
    assert audit["paths"]["planned_paths"] >= 1
    assert expanded["metadata"]["retrieval_stage"] == "evidence_assembler"
    assert expanded["metadata"]["evidence_role"] == "path_edge"
    assert expanded["metadata"]["graph_verified"] is True
    assert expanded["metadata"]["parent_chunk_id"] == related_parent.id
    assert expanded["metadata"]["parent_content"] == related_parent.content
    assert expanded["metadata"]["retrieval_granularity"] == "child_with_parent_context"
    assert expanded["child_content"] == related_chunk.content
    assert expanded["content"] == related_parent.content


@pytest.mark.asyncio
async def test_default_retriever_runs_base_retrieval_before_graph_planning(db_session, sample_course, monkeypatch):
    from app.models import AgentRun
    from app.services import agent_graph
    from app.services.agent_graph import BaseRetrieval

    run = AgentRun(course_id=sample_course.id, question="compare centralities", status="queued")
    db_session.add(run)
    db_session.commit()
    calls = {"hybrid": 0}

    async def fake_hybrid(*args, **kwargs):
        calls["hybrid"] += 1
        return []

    monkeypatch.setattr(agent_graph, "hybrid_search_chunks", fake_hybrid)

    await BaseRetrieval()(
        {
            "db": db_session,
            "run_id": run.id,
            "course_id": sample_course.id,
            "question": "compare centralities",
            "route": "multi_hop_research",
            "filters": SearchFilters(),
            "top_k": 3,
        }
    )
    await BaseRetrieval()(
        {
            "db": db_session,
            "run_id": run.id,
            "course_id": sample_course.id,
            "question": "define centrality",
            "route": "retrieve_notes",
            "filters": SearchFilters(),
            "top_k": 3,
        }
    )

    assert calls["hybrid"] >= 2


def test_api_key_middleware_rejects_missing_key(monkeypatch):
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    monkeypatch.setenv("API_KEYS", "secret-key")
    get_settings.cache_clear()

    client = TestClient(app)
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings/model").status_code == 401
    assert client.get("/api/settings/model?api_key=secret-key").status_code == 401
    assert client.get("/api/settings/model", headers={"X-API-Key": "secret-key"}).status_code == 200

    monkeypatch.delenv("API_KEYS")
    get_settings.cache_clear()
