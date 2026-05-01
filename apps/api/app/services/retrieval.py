from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

from rank_bm25 import BM25Okapi
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.utils import source_type_from_path
from app.core.config import get_settings
from app.models import Chunk, Concept, ConceptRelation, Course, Document, DocumentVersion, IngestionBatch, IngestionJob
from app.schemas import Citation, SearchFilters
from app.services.concept_graph import get_graph_payload
from app.services.embeddings import ChatProvider, EmbeddingProvider, is_degraded_mode
from app.services.parsers import derive_chapter, is_invalid_chapter_label
from app.services.reranker import RerankerProvider
from app.services.vector_store import VectorStore


STORAGE_ALLOWED_SUFFIXES = {
    ".pdf",
    ".ipynb",
    ".md",
    ".markdown",
    ".txt",
    ".docx",
    ".pptx",
    ".ppt",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".html",
    ".htm",
}
STORAGE_EXCLUDED_PARTS = {"output", "tmp", "scripts", ".ipynb_checkpoints", "__pycache__"}
STORAGE_IGNORED_NAMES = {".ds_store"}
TERMINAL_BATCH_STATES = {"completed", "failed", "partial_failed", "skipped"}
QUERY_TYPE_CONFIG = {
    "definition": {"alpha": 0.85, "recall_k": 60},
    "formula": {"alpha": 0.30, "recall_k": 80},
    "example": {"alpha": 0.70, "recall_k": 60},
    "comparison": {"alpha": 0.75, "recall_k": 80},
    "procedure": {"alpha": 0.75, "recall_k": 60},
    "default": {"alpha": 0.72, "recall_k": 64},
}


def should_include_storage_file(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.lower() in STORAGE_IGNORED_NAMES or path.name.startswith("~$"):
        return False
    if path.suffix.lower() not in STORAGE_ALLOWED_SUFFIXES:
        return False
    return not any(part.lower() in STORAGE_EXCLUDED_PARTS for part in path.parts)


def collect_course_storage_paths(course: Course) -> list[Path]:
    root = get_settings().course_paths_for_name(course.name)["storage_root"]
    if not root.exists():
        return []
    return sorted((path for path in root.rglob("*") if should_include_storage_file(path)), key=lambda item: str(item).lower())


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


def tokenize_for_retrieval(text: str) -> list[str]:
    tokens = re.findall(
        r"[a-zA-Z][a-zA-Z0-9_\-]*|[0-9]+(?:\.[0-9]+)?|[α-ωΑ-Ω]+|[\u4e00-\u9fff]|[=<>+\-*/^()]+",
        text.lower(),
    )
    return [token for token in tokens if token.strip() and (len(token) > 1 or re.match(r"[\u4e00-\u9fff=<>+\-*/^()]", token))]


def classify_query_type(query: str) -> str:
    lower = query.lower()
    if any(marker in lower for marker in ("what is", "define", "definition", "meaning", "concept", "什么是", "定义", "概念")):
        return "definition"
    if (
        any(marker in lower for marker in ("formula", "theorem", "proof", "derive", "equation", "complexity", "o(", "公式", "定理", "证明"))
        or re.search(r"[=∑∫√λθπσμ]|p\(|q\(|\\", query)
    ):
        return "formula"
    if any(marker in lower for marker in ("example", "instance", "case", "举例", "例子")):
        return "example"
    if any(marker in lower for marker in ("compare", "versus", "vs", "difference", "relationship", "relate", "区别", "比较", "关系")):
        return "comparison"
    if any(marker in lower for marker in ("algorithm", "procedure", "steps", "how to", "流程", "步骤", "算法")):
        return "procedure"
    return "default"


def query_type_config(query: str) -> dict:
    settings = get_settings()
    query_type = classify_query_type(query)
    config = dict(QUERY_TYPE_CONFIG[query_type])
    if query_type == "formula":
        config["recall_k"] = settings.retrieval_recall_k_formula
    elif query_type == "default":
        config["recall_k"] = settings.retrieval_recall_k_default
    config["query_type"] = query_type
    return config


def normalize_scores(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high == low:
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def clone_for_fusion(item: dict) -> dict:
    clone = item.copy()
    clone["metadata"] = dict(item.get("metadata") or {})
    clone["metadata"]["scores"] = dict(clone["metadata"].get("scores") or {})
    clone["score"] = 0.0
    return clone


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
    settings = get_settings()
    config = query_type_config(query)
    recall_k = max(int(config["recall_k"]), top_k)
    dense_results: list[dict] = []
    if is_degraded_mode() and not settings.enable_model_fallback:
        raise RuntimeError("OPENAI_API_KEY is required for search because ENABLE_MODEL_FALLBACK is false")
    if not is_degraded_mode():
        try:
            dense_results = await dense_search_chunks(db, course_id, query, filters, recall_k)
        except Exception:
            if not settings.enable_model_fallback:
                raise
            dense_results = []
    lexical_results = lexical_search_chunks(db, course_id, query, filters, recall_k)
    if not dense_results:
        return lexical_results[:top_k]
    if not lexical_results:
        return RerankerProvider.get().rerank(query, dense_results, top_k)

    candidates = weighted_score_fusion(
        dense_results,
        lexical_results,
        alpha=float(config["alpha"]),
        top_n=max(recall_k, top_k),
    )
    for item in candidates:
        item.setdefault("metadata", {}).setdefault("scores", {})["query_type"] = config["query_type"]
    return RerankerProvider.get().rerank(query, candidates, top_k)


def weighted_score_fusion(dense_results: list[dict], lexical_results: list[dict], alpha: float, top_n: int) -> list[dict]:
    fused: dict[str, dict] = {}
    dense_values = [float(item.get("metadata", {}).get("scores", {}).get("dense", item["score"])) for item in dense_results]
    lexical_values = [float(item.get("metadata", {}).get("scores", {}).get("bm25", item["score"])) for item in lexical_results]
    dense_norm = normalize_scores(dense_values)
    lexical_norm = normalize_scores(lexical_values)
    for item, normalized_score in zip(dense_results, dense_norm):
        chunk_id = item["chunk_id"]
        fused.setdefault(chunk_id, clone_for_fusion(item))
        scores = fused[chunk_id].setdefault("metadata", {}).setdefault("scores", {})
        scores["dense"] = item.get("metadata", {}).get("scores", {}).get("dense", item["score"])
        scores["dense_norm"] = normalized_score
        scores["fusion_alpha"] = alpha
        fused[chunk_id]["score"] = float(fused[chunk_id].get("score", 0.0)) + (alpha * normalized_score)
    for item, normalized_score in zip(lexical_results, lexical_norm):
        chunk_id = item["chunk_id"]
        fused.setdefault(chunk_id, clone_for_fusion(item))
        scores = fused[chunk_id].setdefault("metadata", {}).setdefault("scores", {})
        lexical_score = item.get("metadata", {}).get("scores", {}).get("bm25", item["score"])
        scores["bm25"] = lexical_score
        scores["lexical"] = item.get("metadata", {}).get("scores", {}).get("lexical", item["score"])
        scores["bm25_norm"] = normalized_score
        scores["fusion_alpha"] = alpha
        fused[chunk_id]["score"] = float(fused[chunk_id].get("score", 0.0)) + ((1.0 - alpha) * normalized_score)

    for item in fused.values():
        scores = item.setdefault("metadata", {}).setdefault("scores", {})
        scores["fused"] = float(item["score"])
    ranked = sorted(fused.values(), key=lambda item: item["score"], reverse=True)
    return ranked[:top_n]


async def graph_enhanced_search(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    base_results = await hybrid_search_chunks(db, course_id, query, filters, top_k)
    if not base_results:
        return []
    merged = {item["chunk_id"]: item for item in base_results}
    base_chunk_ids = list(merged)
    seed_relations = db.scalars(
        select(ConceptRelation).where(
            ConceptRelation.course_id == course_id,
            ConceptRelation.evidence_chunk_id.in_(base_chunk_ids),
        )
    ).all()
    concept_ids = {
        concept_id
        for relation in seed_relations
        for concept_id in (relation.source_concept_id, relation.target_concept_id)
        if concept_id
    }
    if not concept_ids:
        return base_results

    neighbor_relations = db.scalars(
        select(ConceptRelation).where(
            ConceptRelation.course_id == course_id,
            or_(
                ConceptRelation.source_concept_id.in_(concept_ids),
                ConceptRelation.target_concept_id.in_(concept_ids),
            ),
        )
    ).all()
    evidence_ids = {relation.evidence_chunk_id for relation in neighbor_relations if relation.evidence_chunk_id}
    evidence_ids.difference_update(base_chunk_ids)
    if not evidence_ids:
        return base_results

    related_concept_ids = {
        concept_id
        for relation in neighbor_relations
        for concept_id in (relation.source_concept_id, relation.target_concept_id)
        if concept_id
    }
    concepts = {
        concept.id: concept
        for concept in db.scalars(select(Concept).where(Concept.id.in_(related_concept_ids))).all()
    }
    boost_by_chunk: dict[str, float] = {}
    for relation in neighbor_relations:
        if not relation.evidence_chunk_id:
            continue
        source = concepts.get(relation.source_concept_id)
        target = concepts.get(relation.target_concept_id or "")
        importance = max(float(getattr(source, "importance_score", 0.0) or 0.0), float(getattr(target, "importance_score", 0.0) or 0.0))
        boost = float(relation.confidence or 0.0) * importance
        boost_by_chunk[relation.evidence_chunk_id] = max(boost_by_chunk.get(relation.evidence_chunk_id, 0.0), boost)

    chunks = db.scalars(
        select(Chunk).where(
            Chunk.id.in_(evidence_ids),
            Chunk.course_id == course_id,
            Chunk.is_active.is_(True),
        )
    ).all()
    for chunk in chunks:
        document = db.get(Document, chunk.document_id)
        if document is None or document.course_id != course_id or not document.is_active:
            continue
        if filters.chapter and chunk.chapter != filters.chapter:
            continue
        if filters.source_type and chunk.source_type != filters.source_type:
            continue
        if filters.tags and not set(filters.tags).intersection(set(document.tags or [])):
            continue
        graph_boost = boost_by_chunk.get(chunk.id, 0.0)
        item = build_search_payload(chunk, document, query, graph_boost, {"graph_boost": graph_boost})
        item["metadata"]["graph_expanded"] = True
        merged[chunk.id] = item

    for item in merged.values():
        scores = item.setdefault("metadata", {}).setdefault("scores", {})
        if "graph_boost" in scores:
            item["score"] = float(item["score"]) + float(scores["graph_boost"])
    return sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]


def lexical_search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    query_terms = tokenize_for_retrieval(query)
    rows = db.execute(
        select(Chunk, Document)
        .join(Document, Chunk.document_id == Document.id)
        .where(Chunk.course_id == course_id, Chunk.is_active.is_(True), Document.is_active.is_(True))
        .order_by(Chunk.created_at.desc())
    ).all()
    corpus: list[list[str]] = []
    chunk_documents: list[tuple[Chunk, Document]] = []
    for chunk, document in rows:
        if filters.chapter and chunk.chapter != filters.chapter:
            continue
        if filters.source_type and chunk.source_type != filters.source_type:
            continue
        if filters.tags and not set(filters.tags).intersection(set(document.tags or [])):
            continue
        corpus.append(tokenize_for_retrieval(f"{document.title}\n{chunk.section or ''}\n{chunk.content}"))
        chunk_documents.append((chunk, document))
    if not query_terms or not corpus:
        return []
    bm25 = BM25Okapi(corpus)
    bm25_scores = bm25.get_scores(query_terms)
    scored: list[dict] = []
    for idx, (chunk, document) in enumerate(chunk_documents):
        bm25_score = float(bm25_scores[idx])
        overlap = sum(corpus[idx].count(term) for term in query_terms)
        if bm25_score <= 0 and overlap <= 0:
            continue
        score = bm25_score + (0.05 * overlap) + score_chunk_bonus(chunk, document, query)
        scored.append(build_search_payload(chunk, document, query, score, {"bm25": bm25_score, "lexical_overlap": overlap, "lexical": score}))
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
                "storage_root": "",
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
    file_items = list_course_files(db, course.id)
    concepts = db.scalars(select(Concept).where(Concept.course_id == course.id)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course.id)).all()
    batches = db.scalars(select(IngestionBatch).where(IngestionBatch.course_id == course.id).order_by(IngestionBatch.created_at.desc())).all()

    chapter_map: dict[str, list[dict]] = defaultdict(list)
    source_coverage = Counter()
    for item in file_items:
        chapter = item.get("chapter") or "General"
        chapter_map[chapter].append(item)
        source_coverage[item.get("source_type") or "unknown"] += 1

    tree = [
        {
            "id": f"chapter:{chapter}",
            "title": chapter,
            "type": "chapter",
            "children": [
                {"id": item["document_id"] or item["id"], "title": item["title"], "type": "document", "children": []}
                for item in sorted(entries, key=lambda item: item["title"])
            ],
        }
        for chapter, entries in sorted(chapter_map.items())
    ]
    latest_batch = next((batch for batch in batches if batch.status not in TERMINAL_BATCH_STATES), None)
    graph_payload = get_graph_payload(db, course.id)
    return {
        "course": {
            "id": course.id,
            "name": course.name,
            "description": course.description,
            "source_root": str(get_settings().course_paths_for_name(course.name)["storage_root"]),
            "storage_root": str(get_settings().course_paths_for_name(course.name)["storage_root"]),
            "document_count": len(file_items),
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
            "graph_stats": {
                key: value
                for key, value in (latest_batch.stats or {}).items()
                if key.startswith("graph_") or key in {"concepts", "relations"}
            },
            "started_at": latest_batch.started_at,
            "completed_at": latest_batch.completed_at,
        },
        "ingested_document_count": len(file_items),
        "graph_relation_count": len(relations),
        "coverage_by_source_type": dict(source_coverage),
        "degraded_mode": is_degraded_mode(),
    }


ACTIVE_FILE_STATES = {"parsing", "chunking", "embedding", "extracting_graph", "processing"}


def file_status_from_job(job: IngestionJob | None, has_parsed_chunks: bool) -> str:
    if job is None:
        return "parsed" if has_parsed_chunks else "pending"
    if job.status in ACTIVE_FILE_STATES:
        return "parsing"
    if job.status == "queued":
        if (job.stats or {}).get("force_reparse"):
            return "pending"
        return "parsed" if has_parsed_chunks else "pending"
    if job.status == "failed":
        return "failed"
    if job.status == "skipped":
        return "parsed" if has_parsed_chunks else "skipped"
    if job.status == "completed":
        return "parsed" if has_parsed_chunks else "pending"
    return "parsed" if has_parsed_chunks else "pending"


def list_course_files(db: Session, course_id: str) -> list[dict]:
    course = db.get(Course, course_id)
    documents = db.scalars(select(Document).where(Document.course_id == course_id, Document.is_active.is_(True))).all()
    storage_root = get_settings().course_paths_for_name(course.name)["storage_root"] if course is not None else None
    storage_paths = {str(path) for path in collect_course_storage_paths(course)} if course is not None else set()
    document_versions = db.scalars(
        select(DocumentVersion)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(Document.course_id == course_id, Document.is_active.is_(True), DocumentVersion.is_active.is_(True))
    ).all()
    documents_by_id = {document.id: document for document in documents}
    documents_by_storage_path = {
        version.storage_path: documents_by_id[version.document_id]
        for version in document_versions
        if version.document_id in documents_by_id and version.storage_path
    }
    jobs = db.scalars(select(IngestionJob).where(IngestionJob.course_id == course_id).order_by(IngestionJob.updated_at.desc())).all()
    latest_jobs: dict[str, IngestionJob] = {}
    removed_paths: set[str] = set()
    for job in jobs:
        is_removed = (job.error_message or "").startswith("Removed by user") or (job.trigger_source == "remove" and (job.stats or {}).get("removed"))
        if is_removed:
            if job.source_path:
                removed_paths.add(job.source_path)
            continue
        if job.source_path and job.source_path not in latest_jobs:
            latest_jobs[job.source_path] = job

    items: dict[str, dict] = {}
    if course is not None:
        for path in sorted((Path(path_string) for path_string in storage_paths), key=lambda item: str(item).lower()):
            path_string = str(path)
            if path_string in removed_paths:
                continue
            if path_string in items:
                continue
            job = latest_jobs.get(path_string)
            document = documents_by_storage_path.get(path_string)
            chunk_count = db.query(Chunk).filter(Chunk.document_id == document.id, Chunk.is_active.is_(True)).count() if document else 0
            items[path_string] = {
                "id": document.id if document else path_string,
                "document_id": document.id if document else None,
                "title": document.title if document else path.stem or path.name,
                "source_path": path_string,
                "source_type": document.source_type if document else source_type_from_path(path_string),
                "chapter": document.tags[0]
                if document and document.tags and not is_invalid_chapter_label(document.tags[0], course_name=course.name if course else None)
                else derive_chapter(path, course_name=course.name if course else None),
                "status": file_status_from_job(job, has_parsed_chunks=chunk_count > 0),
                "job_state": job.status if job else None,
                "batch_id": job.batch_id if job else None,
                "error": job.error_message if job and job.status == "failed" else None,
                "chunk_count": chunk_count,
                "updated_at": document.updated_at if document else job.updated_at if job else None,
            }

    for path, job in latest_jobs.items():
        if path in removed_paths:
            continue
        if path in items:
            continue
        if storage_root is not None:
            continue
        items[path] = {
            "id": job.id,
            "document_id": job.document_id,
            "title": Path(path).stem or Path(path).name,
            "source_path": path,
            "source_type": source_type_from_path(path),
            "chapter": None,
            "status": file_status_from_job(job, has_parsed_chunks=False),
            "job_state": job.status,
            "batch_id": job.batch_id,
            "error": job.error_message,
            "chunk_count": 0,
            "updated_at": job.updated_at,
        }

    latest_batch = db.scalar(select(IngestionBatch).where(IngestionBatch.course_id == course_id).order_by(IngestionBatch.created_at.desc()))
    uploaded_paths = (latest_batch.stats or {}).get("uploaded_files", []) if latest_batch else []
    for path in uploaded_paths:
        if path in removed_paths:
            continue
        if path in items:
            continue
        if storage_root is not None:
            continue
        items[path] = {
            "id": path,
            "document_id": None,
            "title": Path(path).stem or Path(path).name,
            "source_path": path,
            "source_type": source_type_from_path(path),
            "chapter": None,
            "status": "pending",
            "job_state": None,
            "batch_id": latest_batch.id,
            "error": None,
            "chunk_count": 0,
            "updated_at": latest_batch.created_at,
        }

    status_rank = {"parsing": 0, "pending": 1, "failed": 2, "parsed": 3, "skipped": 4}
    return sorted(items.values(), key=lambda item: (status_rank.get(item["status"], 9), item["title"].lower()))


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
