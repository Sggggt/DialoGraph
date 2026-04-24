from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chunk, Concept, ConceptRelation, Course, Document, IngestionBatch, IngestionJob
from app.schemas import Citation, SearchFilters
from app.services.concept_graph import get_graph_payload
from app.services.embeddings import ChatProvider, EmbeddingProvider, is_degraded_mode
from app.services.vector_store import VectorStore


def score_chunk_bonus(chunk: Chunk, document: Document, query: str) -> float:
    kind = (chunk.metadata_json or {}).get("content_kind")
    title_text = f"{document.title}\n{chunk.section or ''}".lower()
    bonus = 0.0
    if kind in {"markdown", "text", "pdf_page", "slide", "doc_section"}:
        bonus += 1.1
    if kind == "code":
        bonus -= 1.8
    if kind == "output":
        bonus -= 0.8
    if query.lower() in title_text:
        bonus += 1.4
    if chunk.section and query.lower() in chunk.section.lower():
        bonus += 0.7
    return bonus


def build_search_payload(chunk: Chunk, document: Document, query: str, score: float, scores: dict | None = None) -> dict:
    citation = Citation(
        chunk_id=chunk.id,
        document_id=document.id,
        document_title=document.title,
        source_path=document.source_path,
        chapter=chunk.chapter,
        section=chunk.section,
        page_number=chunk.page_number,
        snippet=chunk.snippet,
    )
    metadata = chunk.metadata_json | {"chapter": chunk.chapter, "source_type": chunk.source_type}
    if scores:
        metadata["scores"] = scores
    return {
        "chunk_id": chunk.id,
        "snippet": chunk.snippet,
        "score": score,
        "citations": [citation.model_dump()],
        "metadata": metadata,
        "content": chunk.content,
        "document_title": document.title,
        "source_path": document.source_path,
        "chapter": chunk.chapter,
        "source_type": chunk.source_type,
    }


async def dense_search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    course = db.get(Course, course_id)
    if course is None:
        return []
    embedder = EmbeddingProvider()
    vectors = await embedder.embed_texts([query], text_type="query")
    vector_store = VectorStore(course_name=course.name)
    results = vector_store.search(
        vector=vectors[0],
        limit=max(top_k * 3, top_k),
        filters={
            "course_id": course_id,
            "chapter": filters.chapter,
            "difficulty": filters.difficulty,
            "source_type": filters.source_type,
        },
    )
    payloads = []
    for result in results:
        chunk = db.get(Chunk, result["id"])
        if chunk is None or chunk.course_id != course_id or not chunk.is_active:
            continue
        document = db.get(Document, chunk.document_id)
        if document is None or document.course_id != course_id:
            continue
        if filters.tags and not set(filters.tags).intersection(set(document.tags or [])):
            continue
        dense_score = float(result["score"])
        score = dense_score + score_chunk_bonus(chunk, document, query)
        payloads.append(build_search_payload(chunk, document, query, score, {"dense": dense_score}))
    payloads.sort(key=lambda item: item["score"], reverse=True)
    return payloads[:top_k]


async def search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    return await hybrid_search_chunks(db, course_id, query, filters, top_k)


async def hybrid_search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    dense_results: list[dict] = []
    if not is_degraded_mode():
        try:
            dense_results = await dense_search_chunks(db, course_id, query, filters, max(top_k * 4, top_k))
        except Exception:
            dense_results = []
    lexical_results = lexical_search_chunks(db, course_id, query, filters, max(top_k * 4, top_k))
    if not dense_results:
        return lexical_results[:top_k]
    if not lexical_results:
        return dense_results[:top_k]

    rrf_k = 60
    fused: dict[str, dict] = {}
    for rank, item in enumerate(dense_results, start=1):
        chunk_id = item["chunk_id"]
        fused.setdefault(chunk_id, item)
        scores = fused[chunk_id].setdefault("metadata", {}).setdefault("scores", {})
        scores["dense"] = item.get("metadata", {}).get("scores", {}).get("dense", item["score"])
        scores["rrf_dense"] = 1 / (rrf_k + rank)
    for rank, item in enumerate(lexical_results, start=1):
        chunk_id = item["chunk_id"]
        fused.setdefault(chunk_id, item)
        scores = fused[chunk_id].setdefault("metadata", {}).setdefault("scores", {})
        scores["lexical"] = item.get("metadata", {}).get("scores", {}).get("lexical", item["score"])
        scores["rrf_lexical"] = 1 / (rrf_k + rank)

    for item in fused.values():
        scores = item.setdefault("metadata", {}).setdefault("scores", {})
        fused_score = float(scores.get("rrf_dense", 0.0)) + float(scores.get("rrf_lexical", 0.0))
        scores["fused"] = fused_score
        item["score"] = fused_score + (0.001 * item.get("score", 0.0))
    ranked = sorted(fused.values(), key=lambda item: item["score"], reverse=True)
    return ranked[:top_k]


def lexical_search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    query_terms = [term for term in re.findall(r"[a-zA-Z0-9_]+", query.lower()) if len(term) > 2]
    chunks = db.scalars(
        select(Chunk)
        .where(Chunk.course_id == course_id, Chunk.is_active.is_(True))
        .order_by(Chunk.created_at.desc())
    ).all()
    scored: list[dict] = []
    for chunk in chunks:
        document = db.get(Document, chunk.document_id)
        if document is None:
            continue
        if filters.chapter and chunk.chapter != filters.chapter:
            continue
        if filters.source_type and chunk.source_type != filters.source_type:
            continue
        if filters.tags and not set(filters.tags).intersection(set(document.tags or [])):
            continue
        section_text = chunk.section or ""
        haystack = f"{document.title}\n{section_text}\n{chunk.content}".lower()
        overlap = sum(haystack.count(term) for term in query_terms)
        if overlap <= 0 and query.lower() not in haystack:
            continue
        score = float(overlap + (3 if query.lower() in haystack else 0)) + score_chunk_bonus(chunk, document, query)
        scored.append(build_search_payload(chunk, document, query, score, {"lexical": score}))
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:top_k]


async def answer_question(db: Session, course_id: str, question: str, filters: SearchFilters, top_k: int, history: list[dict]) -> dict:
    results = await search_chunks(db, course_id, question, filters, top_k)
    chat = ChatProvider()
    answer = await chat.answer_question(question, results, history)
    return {
        "answer": answer,
        "citations": [citation for result in results for citation in result["citations"]],
        "used_chunks": results,
        "degraded_mode": is_degraded_mode(),
    }


def get_dashboard_snapshot(db: Session, course_id: str) -> dict:
    course = db.get(Course, course_id)
    if course is None:
        return {
            "course": {
                "id": "empty",
                "name": "Course Workspace",
                "description": None,
                "source_root": "",
                "document_count": 0,
                "concept_count": 0,
                "degraded_mode": is_degraded_mode(),
            },
            "tree": [],
            "graph": {"nodes": [], "edges": [], "focus_chapter": None},
            "batch_status": None,
            "ingested_document_count": 0,
            "graph_relation_count": 0,
            "coverage_by_source_type": {},
            "degraded_mode": is_degraded_mode(),
        }

    documents = db.scalars(select(Document).where(Document.course_id == course.id, Document.is_active.is_(True))).all()
    concepts = db.scalars(select(Concept).where(Concept.course_id == course.id)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course.id)).all()
    batches = db.scalars(select(IngestionBatch).where(IngestionBatch.course_id == course.id).order_by(IngestionBatch.created_at.desc())).all()

    chapter_map: dict[str, list[Document]] = defaultdict(list)
    source_coverage = Counter()
    for document in documents:
        chapter = document.tags[0] if document.tags else "General"
        chapter_map[chapter].append(document)
        source_coverage[document.source_type] += 1

    tree = [
        {
            "id": f"chapter:{chapter}",
            "title": chapter,
            "type": "chapter",
            "children": [
                {"id": document.id, "title": document.title, "type": "document", "children": []}
                for document in sorted(entries, key=lambda item: item.title)
            ],
        }
        for chapter, entries in sorted(chapter_map.items())
    ]
    latest_batch = batches[0] if batches else None
    graph_payload = get_graph_payload(db, course.id)
    return {
        "course": {
            "id": course.id,
            "name": course.name,
            "description": course.description,
            "source_root": course.source_root,
            "document_count": len(documents),
            "concept_count": len(concepts),
            "degraded_mode": is_degraded_mode(),
        },
        "tree": tree,
        "graph": graph_payload,
        "batch_status": None
        if latest_batch is None
        else {
            "batch_id": latest_batch.id,
            "state": latest_batch.status,
            "trigger_source": latest_batch.trigger_source,
            "source_root": latest_batch.source_root,
            "total_files": latest_batch.total_files,
            "processed_files": latest_batch.processed_files,
            "success_count": latest_batch.success_count,
            "failure_count": latest_batch.failure_count,
            "skipped_count": latest_batch.skipped_count,
            "coverage_by_source_type": (latest_batch.stats or {}).get("coverage_by_source_type", {}),
            "errors": (latest_batch.stats or {}).get("errors", []),
            "started_at": latest_batch.started_at,
            "completed_at": latest_batch.completed_at,
        },
        "ingested_document_count": len(documents),
        "graph_relation_count": len(relations),
        "coverage_by_source_type": dict(source_coverage),
        "degraded_mode": is_degraded_mode(),
    }


def get_job_status(db: Session, job_id: str) -> dict | None:
    job = db.get(IngestionJob, job_id)
    if job is None:
        return None
    return {
        "job_id": job.id,
        "state": job.status,
        "error": job.error_message,
        "document_id": job.document_id,
        "source_path": job.source_path,
        "batch_id": job.batch_id,
        "stats": job.stats,
    }
