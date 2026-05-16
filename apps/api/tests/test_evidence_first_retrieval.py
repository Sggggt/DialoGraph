from __future__ import annotations


def _search_item(chunk, score: float = 0.9) -> dict:
    return {
        "chunk_id": chunk.id,
        "snippet": chunk.snippet,
        "score": score,
        "citations": [],
        "metadata": {"scores": {"fused": score}, "quality_action": "retrieval_candidate"},
        "content": chunk.content,
        "document_title": "doc",
        "source_path": "doc.md",
        "chapter": chunk.chapter,
        "source_type": chunk.source_type,
    }


def test_evidence_first_planner_uses_only_verified_edges(db_session, sample_course, indexed_chunks):
    from app.models import Concept, ConceptRelation
    from app.schemas import SearchFilters
    from app.services.retrieval import (
        assemble_evidence_documents,
        controlled_graph_enhancement,
        plan_evidence_chains,
        select_evidence_anchors,
    )

    _, chunks = indexed_chunks
    source = Concept(
        course_id=sample_course.id,
        canonical_name="Degree Centrality",
        normalized_name="degree centrality",
        evidence_count=2,
        graph_rank_score=0.8,
    )
    target = Concept(
        course_id=sample_course.id,
        canonical_name="Closeness Centrality",
        normalized_name="closeness centrality",
        evidence_count=2,
        graph_rank_score=0.7,
    )
    noisy = Concept(
        course_id=sample_course.id,
        canonical_name="Noisy Similarity",
        normalized_name="noisy similarity",
        evidence_count=1,
        graph_rank_score=0.1,
    )
    db_session.add_all([source, target, noisy])
    db_session.flush()
    verified = ConceptRelation(
        course_id=sample_course.id,
        source_concept_id=source.id,
        target_concept_id=target.id,
        target_name=target.canonical_name,
        relation_type="compares_with",
        evidence_chunk_id=chunks[1].id,
        confidence=0.9,
        weight=0.9,
        relation_source="llm",
        is_validated=True,
        metadata_json={"evidence_source_match": True, "evidence_target_match": True},
    )
    semantic_sparse = ConceptRelation(
        course_id=sample_course.id,
        source_concept_id=source.id,
        target_concept_id=noisy.id,
        target_name=noisy.canonical_name,
        relation_type="related_to",
        evidence_chunk_id=chunks[0].id,
        confidence=0.95,
        weight=0.95,
        relation_source="semantic_sparse",
        is_validated=True,
        metadata_json={"evidence_source_match": True, "evidence_target_match": True},
    )
    db_session.add_all([verified, semantic_sparse])
    db_session.commit()

    anchors, anchor_audit = select_evidence_anchors(db_session, sample_course.id, [_search_item(chunks[1])])
    assert anchor_audit["anchor_count"] == 1
    assert anchors[0]["metadata"]["graph_verified"] is True

    paths, path_audit = plan_evidence_chains(db_session, sample_course.id, anchors, query_type="comparison")
    assert path_audit["planned_paths"] == 1
    assert paths[0]["relation_ids"] == [verified.id]
    assert semantic_sparse.id not in paths[0]["relation_ids"]

    enhanced, graph_audit = controlled_graph_enhancement(
        db_session,
        sample_course.id,
        "compare centrality",
        filters=SearchFilters(),
        base_chunk_ids={chunks[0].id},
        paths=paths,
    )
    assert graph_audit["graph_enhanced_chunks"] == 1
    assert enhanced[0]["metadata"]["evidence_role"] == "path_edge"
    assert enhanced[0]["metadata"]["graph_verified"] is True

    assembled, assembly_audit = assemble_evidence_documents([_search_item(chunks[0])], anchors, enhanced, top_k=3)
    assert assembly_audit["graph_documents"] == 1
    assert {item["chunk_id"] for item in assembled} >= {chunks[0].id, chunks[1].id}
