from __future__ import annotations

import os

import pytest


def test_graph_judge_evidence_is_read_only_and_reports_invalid_refs(db_session, sample_course):
    from app.models import Chunk, Concept, Document, DocumentVersion
    from app.services.graph_judge import build_graph_judge_evidence

    document = Document(
        course_id=sample_course.id,
        title="Lecture 1",
        source_path="Lecture 1.pdf",
        source_type="pdf",
        tags=["Lecture 1"],
        checksum="checksum",
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version=1,
        checksum="checksum",
        storage_path="Lecture 1.pdf",
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
            content="Bayes theorem connects prior likelihood and posterior.",
            snippet="Bayes theorem connects prior likelihood and posterior.",
            chapter="Lecture 1",
            section="Bayes",
            source_type="pdf",
            metadata_json={"content_kind": "pdf_page"},
            embedding_status="ready",
        )
    )
    db_session.add(
        Concept(
            course_id=sample_course.id,
            canonical_name="Bayes' Theorem",
            normalized_name="bayes theorem",
            summary="Bayesian update rule.",
            chapter_refs=["20260425"],
            importance_score=0.9,
        )
    )
    db_session.commit()

    before = db_session.query(Concept).count()
    evidence = build_graph_judge_evidence(db_session, sample_course.id)
    after = db_session.query(Concept).count()

    assert before == after
    assert evidence["invalid_chapter_refs"] == ["20260425"]
    assert evidence["concept_count"] == 1


def test_typed_graph_exports_are_separated(db_session, sample_course):
    from app.models import Chunk, Concept, ConceptAlias, ConceptRelation, Document, DocumentVersion
    from app.services.concept_graph import get_graph_payload

    document = Document(
        course_id=sample_course.id,
        title="Lecture 1",
        source_path="Lecture 1.pdf",
        source_type="pdf",
        tags=["Lecture 1"],
        checksum="typed-graph",
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version=1,
        checksum="typed-graph",
        storage_path="Lecture 1.pdf",
        extracted_path=None,
        is_active=True,
    )
    db_session.add(version)
    db_session.flush()
    chunk = Chunk(
        course_id=sample_course.id,
        document_id=document.id,
        document_version_id=version.id,
        content="PageRank is an algorithm for ranking graph nodes.",
        snippet="PageRank is an algorithm for ranking graph nodes.",
        chapter="Lecture 1",
        section="Ranking",
        source_type="pdf",
        metadata_json={"content_kind": "pdf_page"},
        embedding_status="ready",
    )
    source = Concept(
        course_id=sample_course.id,
        canonical_name="PageRank",
        normalized_name="pagerank::algorithm",
        concept_type="algorithm",
        summary="Graph ranking algorithm.",
        chapter_refs=["Lecture 1"],
        importance_score=0.9,
        evidence_count=2,
    )
    target = Concept(
        course_id=sample_course.id,
        canonical_name="Graph Centrality",
        normalized_name="graph centrality::concept",
        concept_type="concept",
        summary="Node importance concept.",
        chapter_refs=["Lecture 1"],
        importance_score=0.8,
        evidence_count=2,
    )
    db_session.add_all([chunk, source, target])
    db_session.flush()
    db_session.add(ConceptAlias(concept_id=source.id, alias="PageRank", normalized_alias="pagerank::algorithm"))
    db_session.add(
        ConceptRelation(
            course_id=sample_course.id,
            source_concept_id=source.id,
            target_concept_id=target.id,
            target_name=target.canonical_name,
            relation_type="is_a",
            evidence_chunk_id=chunk.id,
            confidence=0.9,
            is_validated=True,
            weight=0.9,
            relation_source="llm",
        )
    )
    db_session.commit()

    semantic = get_graph_payload(db_session, sample_course.id, graph_type="semantic")
    structural = get_graph_payload(db_session, sample_course.id, graph_type="structural")
    evidence = get_graph_payload(db_session, sample_course.id, graph_type="evidence")

    assert semantic["graph_type"] == "semantic"
    assert {node["category"] for node in semantic["nodes"]} == {"semantic_entity"}
    assert all(edge["category"] == "semantic" for edge in semantic["edges"])
    assert "algorithm" in {node["entity_type"] for node in semantic["nodes"]}

    assert structural["graph_type"] == "structural"
    assert "semantic_entity" not in {node["category"] for node in structural["nodes"]}
    assert all(edge["category"] == "structure" for edge in structural["edges"])

    assert evidence["graph_type"] == "evidence"
    assert {node["category"] for node in evidence["nodes"]}.issubset({"semantic_entity", "evidence_chunk", "document_version"})
    assert all(edge["category"] == "evidence" for edge in evidence["edges"])


@pytest.mark.skipif(os.getenv("RUN_LLM_JUDGE") != "1", reason="set RUN_LLM_JUDGE=1 to call the configured chat model")
@pytest.mark.asyncio
async def test_graph_judge_llm_endpoint_returns_verdict(db_session, sample_course):
    from app.services.graph_judge import run_graph_judge

    result = await run_graph_judge(db_session, sample_course.id)

    assert "verdict" in result
