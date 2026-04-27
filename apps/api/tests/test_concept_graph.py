from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select


def make_chunk(chunk_id: str, document_id: str, content: str, content_kind: str = "pdf_page"):
    return SimpleNamespace(
        id=chunk_id,
        document_id=document_id,
        content=content,
        chapter="Lecture 1",
        source_type="pdf",
        metadata_json={"content_kind": content_kind},
    )


def test_choose_llm_graph_chunks_spreads_across_documents():
    from app.services.concept_graph import choose_llm_graph_chunks

    chunks = [
        make_chunk("a1", "doc-a", "short"),
        make_chunk("a2", "doc-a", "long " * 400),
        make_chunk("b1", "doc-b", "medium " * 120),
        make_chunk("c1", "doc-c", "medium " * 80),
    ]

    selected = choose_llm_graph_chunks(chunks, limit=3, chunks_per_document=1)

    assert len(selected) == 3
    assert "a2" in selected
    assert len({chunk.document_id for chunk in chunks if chunk.id in selected}) == 3


def test_choose_llm_graph_chunks_fills_remaining_slots():
    from app.services.concept_graph import choose_llm_graph_chunks

    chunks = [
        make_chunk("a1", "doc-a", "short"),
        make_chunk("a2", "doc-a", "long " * 400),
        make_chunk("a3", "doc-a", "medium " * 120),
    ]

    selected = choose_llm_graph_chunks(chunks, limit=2, chunks_per_document=1)

    assert selected == {"a2", "a3"}


def test_choose_llm_graph_chunks_uses_runtime_settings(no_fallback_env, monkeypatch):
    from app.core.config import get_settings
    from app.services.concept_graph import choose_llm_graph_chunks

    monkeypatch.setenv("GRAPH_EXTRACTION_CHUNK_LIMIT", "5")
    monkeypatch.setenv("GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT", "2")
    get_settings.cache_clear()
    chunks = [
        make_chunk(f"{document}-{index}", document, "content " * (50 + index))
        for document in ("doc-a", "doc-b", "doc-c")
        for index in range(3)
    ]

    selected = choose_llm_graph_chunks(chunks)

    assert len(selected) == 5
    assert len({chunk.document_id for chunk in chunks if chunk.id in selected}) >= 3


@pytest.mark.asyncio
async def test_rebuild_course_graph_reports_real_llm_selection_stats(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.models import Chunk, Document, DocumentVersion
    from app.services import concept_graph

    monkeypatch.setenv("GRAPH_EXTRACTION_CHUNK_LIMIT", "5")
    monkeypatch.setenv("GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT", "2")
    get_settings.cache_clear()

    for document_index in range(3):
        document = Document(
            course_id=sample_course.id,
            title=f"Lecture {document_index + 1}",
            source_path=f"Lecture {document_index + 1}.pdf",
            source_type="pdf",
            tags=["20260425"] if document_index == 0 else [f"Lecture {document_index + 1}"],
            checksum=f"checksum-{document_index}",
        )
        db_session.add(document)
        db_session.flush()
        version = DocumentVersion(
            document_id=document.id,
            version=1,
            checksum=document.checksum,
            storage_path=document.source_path,
            extracted_path=None,
            is_active=True,
        )
        db_session.add(version)
        db_session.flush()
        for chunk_index in range(3):
            db_session.add(
                Chunk(
                    course_id=sample_course.id,
                    document_id=document.id,
                    document_version_id=version.id,
                    content=f"Bayesian inference posterior prior likelihood {document_index} {chunk_index} " * 20,
                    snippet="Bayesian inference posterior prior likelihood",
                    chapter="20260425" if document_index == 0 else document.tags[0],
                    section="Topic",
                    source_type="pdf",
                    metadata_json={"content_kind": "pdf_page"},
                    embedding_status="ready",
                )
            )
    db_session.commit()

    async def fake_upsert(db, course_id, chunk, use_llm=True, llm_payload=None):
        return (1 if use_llm else 0, 1 if use_llm else 0)

    async def fake_extract_payloads(chunks, concurrency=4):
        return {chunk.id: {"concepts": [{"name": f"Concept {chunk.id}", "aliases": [], "summary": "", "concept_type": "concept", "importance_score": 0.8}], "relations": []} for chunk in chunks}

    monkeypatch.setattr(concept_graph, "upsert_concepts_from_chunk", fake_upsert)
    monkeypatch.setattr(concept_graph, "extract_llm_graph_payloads", fake_extract_payloads)

    stats = await concept_graph.rebuild_course_graph(db_session, sample_course.id)

    assert stats["graph_extraction_chunk_limit"] == 5
    assert stats["graph_extraction_chunks_per_document"] == 2
    assert stats["graph_llm_selected_chunks"] == 5
    assert stats["graph_llm_success_chunks"] == 5
    assert stats["graph_llm_source_documents"] == 3
    refreshed_document = db_session.scalar(select(Document).where(Document.title == "Lecture 1"))
    refreshed_chunk = db_session.scalar(select(Chunk).where(Chunk.document_id == refreshed_document.id))
    assert refreshed_document.tags == ["Lecture 1"]
    assert refreshed_chunk.chapter == "Lecture 1"


def test_invalid_chapter_refs_are_not_added_to_concepts(db_session, sample_course):
    from app.services.concept_graph import get_or_create_concept

    concept, _ = get_or_create_concept(
        db_session,
        sample_course.id,
        name="Posterior Distribution",
        chapter="20260425",
        summary="A distribution after observing evidence.",
        aliases=[],
        concept_type="concept",
        importance_score=0.9,
    )

    assert concept.chapter_refs == []


def test_document_chapter_label_prefers_canonical_filename_over_stale_tags(sample_course):
    from app.models import Document
    from app.services.concept_graph import document_chapter_label

    lab_document = Document(
        course_id=sample_course.id,
        title="Labs solutions",
        source_path=r"C:\data\Algorithmic GT\storage\20260425\Labs solutions.pdf",
        source_type="pdf",
        tags=["Labs solutions"],
        checksum="checksum",
    )
    visualizer_document = Document(
        course_id=sample_course.id,
        title="graph_algorithms_visualizer",
        source_path=r"C:\data\Algorithmic GT\storage\20260425\graph_algorithms_visualizer.html",
        source_type="html",
        tags=["graph algorithms visualizer"],
        checksum="checksum",
    )

    assert document_chapter_label(lab_document, "Algorithmic GT") == "Lab Solutions"
    assert document_chapter_label(visualizer_document, "Algorithmic GT") == "Reference"


@pytest.mark.asyncio
async def test_extract_llm_graph_payloads_isolates_chunk_failures(monkeypatch):
    from app.services import concept_graph

    class FakeChatProvider:
        async def extract_graph_payload(self, text, chapter, source_type):
            if "bad" in text:
                raise RuntimeError("model timeout")
            return {"concepts": [{"name": "Good Concept"}], "relations": []}

    monkeypatch.setattr(concept_graph, "ChatProvider", FakeChatProvider)

    payloads, errors = await concept_graph.extract_llm_graph_payloads(
        [
            make_chunk("ok", "doc-a", "good content"),
            make_chunk("bad", "doc-a", "bad content"),
        ],
        concurrency=2,
    )

    assert set(payloads) == {"ok"}
    assert set(errors) == {"bad"}
    assert "model timeout" in errors["bad"]


def test_sync_graph_chapter_labels_normalizes_existing_refs(db_session, sample_course):
    from app.models import Chunk, Concept, ConceptRelation, Document, DocumentVersion
    from app.services.concept_graph import sync_graph_chapter_labels

    document = Document(
        course_id=sample_course.id,
        title="Labs solutions",
        source_path=r"C:\data\Unit Test Course\storage\20260425\Labs solutions.pdf",
        source_type="pdf",
        tags=["Labs solutions"],
        checksum="checksum",
    )
    db_session.add(document)
    db_session.flush()
    version = DocumentVersion(
        document_id=document.id,
        version=1,
        checksum="checksum",
        storage_path=document.source_path,
        extracted_path=None,
        is_active=True,
    )
    db_session.add(version)
    db_session.flush()
    chunk = Chunk(
        course_id=sample_course.id,
        document_id=document.id,
        document_version_id=version.id,
        content="Lab solution content",
        snippet="Lab solution content",
        chapter="Labs solutions",
        section="Lab",
        source_type="pdf",
        metadata_json={"content_kind": "pdf_page"},
        embedding_status="ready",
    )
    concept = Concept(
        course_id=sample_course.id,
        canonical_name="Minimum Cut",
        normalized_name="minimum cut",
        summary="",
        chapter_refs=["Labs solutions", "20260425"],
        importance_score=0.8,
    )
    empty_ref_concept = Concept(
        course_id=sample_course.id,
        canonical_name="Residual Network",
        normalized_name="residual network",
        summary="",
        chapter_refs=[],
        importance_score=0.8,
    )
    db_session.add_all([chunk, concept, empty_ref_concept])
    db_session.flush()
    db_session.add(
        ConceptRelation(
            course_id=sample_course.id,
            source_concept_id=concept.id,
            target_concept_id=empty_ref_concept.id,
            target_name=empty_ref_concept.canonical_name,
            relation_type="relates_to",
            evidence_chunk_id=chunk.id,
            confidence=0.9,
            extraction_method="llm",
        )
    )
    db_session.commit()

    stats = sync_graph_chapter_labels(db_session, sample_course.id)

    db_session.refresh(document)
    db_session.refresh(chunk)
    db_session.refresh(concept)
    db_session.refresh(empty_ref_concept)
    assert stats["updated_documents"] == 1
    assert document.tags == ["Lab Solutions"]
    assert chunk.chapter == "Lab Solutions"
    assert concept.chapter_refs == ["Lab Solutions"]
    assert empty_ref_concept.chapter_refs == ["Lab Solutions"]
