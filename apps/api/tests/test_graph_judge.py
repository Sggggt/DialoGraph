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


@pytest.mark.skipif(os.getenv("RUN_LLM_JUDGE") != "1", reason="set RUN_LLM_JUDGE=1 to call the configured chat model")
@pytest.mark.asyncio
async def test_graph_judge_llm_endpoint_returns_verdict(db_session, sample_course):
    from app.services.graph_judge import run_graph_judge

    result = await run_graph_judge(db_session, sample_course.id)

    assert "verdict" in result
