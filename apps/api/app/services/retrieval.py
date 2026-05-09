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
from app.services.cache_manager import get_cache_manager
from app.services.embeddings import ChatProvider, EmbeddingProvider, is_degraded_mode
from app.services.parsers import derive_chapter, is_invalid_chapter_label
from app.services.reranker import get_reranker
from app.services.runtime_settings import read_env_bool
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
STORAGE_EXCLUDED_PARTS = {"output", "scripts", ".ipynb_checkpoints", "__pycache__"}
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
RETRIEVAL_LAYER_CONFIG = {
    ("definition", "retrieve_notes"): 1,
    ("formula", "retrieve_notes"): 1,
    ("definition", "retrieve_both"): 1,
    ("formula", "retrieve_both"): 1,
    ("example", "retrieve_notes"): 2,
    ("example", "retrieve_both"): 2,
    ("procedure", "retrieve_notes"): 2,
    ("procedure", "retrieve_both"): 2,
    ("comparison", "multi_hop_research"): 3,
    ("default", "multi_hop_research"): 3,
    ("comparison", "retrieve_both"): 3,
    ("default", "retrieve_notes"): 2,
    ("default", "retrieve_both"): 2,
}
PRIMARY_SCORE_KEYS = ("dense", "lexical", "fused", "rerank", "lightweight_rerank", "term_overlap_ratio")


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
    from app.services.chinese_text import tokenize_for_retrieval as _cn_tokenize

    return _cn_tokenize(text)


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
    if any(marker in lower for marker in ("algorithm", "procedure", "steps", "how to", "流程", "步骤", "算法", "如何")):
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


def default_model_audit() -> dict:
    settings = get_settings()
    return {
        "embedding_provider": "none",
        "embedding_model": settings.embedding_model,
        "embedding_external_called": False,
        "embedding_fallback_reason": None,
        "reranker_enabled": read_env_bool("RERANKER_ENABLED", settings.reranker_enabled),
        "reranker_called": False,
        "fallback_enabled": settings.enable_model_fallback,
        "degraded_mode": is_degraded_mode(),
        "vector_index_warning": None,
    }


def did_rerank(results: list[dict]) -> bool:
    return any(
        item.get("metadata", {}).get("scores", {}).get("rerank") is not None
        or item.get("metadata", {}).get("scores", {}).get("cross_encoder") is not None
        for item in results
    )


def attach_model_audit(results: list[dict], audit: dict) -> list[dict]:
    for item in results:
        metadata = item.setdefault("metadata", {})
        scores = metadata.setdefault("scores", {})
        for key in PRIMARY_SCORE_KEYS:
            scores.setdefault(key, None)
        metadata["model_audit"] = dict(audit)
    return results


def is_parent_chunk(chunk: Chunk) -> bool:
    return bool((chunk.metadata_json or {}).get("is_parent"))


def is_child_retrieval_candidate(chunk: Chunk, db: Session) -> bool:
    if not is_parent_chunk(chunk):
        return True
    # Parent chunks with no children are valid retrieval candidates themselves
    # (they are the finest-grained unit for that document segment).
    has_children = db.scalar(
        select(1).where(Chunk.parent_chunk_id == chunk.id).limit(1)
    ) is not None
    return not has_children


def expand_results_with_parent_context(db: Session, course_id: str, results: list[dict]) -> list[dict]:
    if not results:
        return results
    parent_ids = {
        str(item.get("metadata", {}).get("parent_chunk_id") or "")
        for item in results
        if item.get("metadata", {}).get("parent_chunk_id")
    }
    if not parent_ids:
        return results
    parents = {
        chunk.id: chunk
        for chunk in db.scalars(
            select(Chunk).where(
                Chunk.id.in_(parent_ids),
                Chunk.course_id == course_id,
                Chunk.is_active.is_(True),
            )
        ).all()
    }
    for item in results:
        metadata = item.setdefault("metadata", {})
        parent_id = metadata.get("parent_chunk_id")
        parent = parents.get(parent_id)
        if parent is None:
            continue
        metadata["parent_content"] = parent.content
        metadata["parent_snippet"] = parent.snippet
        metadata["parent_section"] = parent.section
        metadata["retrieval_granularity"] = "child_with_parent_context"
        item["child_content"] = item.get("content")
        item["content"] = parent.content
    return results


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
    metadata = (chunk.metadata_json or {}) | {"chapter": chunk.chapter, "source_type": chunk.source_type}
    metadata["is_parent"] = is_parent_chunk(chunk)
    if chunk.parent_chunk_id:
        metadata["parent_chunk_id"] = str(chunk.parent_chunk_id)
        metadata["retrieval_granularity"] = "child"
    if scores:
        metadata["scores"] = scores
    return {
        "chunk_id": chunk.id,
        "snippet": chunk.snippet,
        "score": score,
        "citations": [citation.model_dump()],
        "metadata": metadata,
        "content": chunk.content,
        "child_content": chunk.content if chunk.parent_chunk_id else None,
        "document_title": document.title,
        "source_path": document.source_path,
        "chapter": chunk.chapter,
        "source_type": chunk.source_type,
    }


async def dense_search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int, model_audit: dict | None = None) -> list[dict]:
    course = db.get(Course, course_id)
    if course is None:
        return []
    embedder = EmbeddingProvider()
    cache = get_cache_manager()
    embedding_version = "contextual_enriched_v2"
    cached_vector = cache.get_embedding(course_id, query, embedding_version)
    if cached_vector is not None:
        embedding_result = type("obj", (object,), {"vectors": [cached_vector], "provider": "cache", "external_called": False, "fallback_reason": None})()
    else:
        embedding_result = await embedder.embed_texts_with_meta([query], text_type="query")
        if embedding_result.vectors and embedding_result.external_called:
            cache.set_embedding(course_id, query, embedding_version, embedding_result.vectors[0])
    if model_audit is not None:
        model_audit.update(
            {
                "embedding_provider": embedding_result.provider,
                "embedding_external_called": embedding_result.external_called,
                "embedding_fallback_reason": embedding_result.fallback_reason,
            }
        )
    vector_store = VectorStore(course_name=course.name)
    results = vector_store.search(
        vector=embedding_result.vectors[0],
        limit=max(top_k * 3, top_k),
        filters={
            "course_id": course_id,
            "chapter": filters.chapter,
            "difficulty": filters.difficulty,
            "source_type": filters.source_type,
        },
    )
    payloads = []
    dense_scores: list[float] = []
    for result in results:
        chunk = db.get(Chunk, result["id"])
        if chunk is None or chunk.course_id != course_id or not chunk.is_active or not is_child_retrieval_candidate(chunk, db):
            continue
        document = db.get(Document, chunk.document_id)
        if document is None or document.course_id != course_id:
            continue
        if filters.tags and not set(filters.tags).intersection(set(document.tags or [])):
            continue
        dense_score = float(result["score"])
        dense_scores.append(dense_score)
        score = dense_score + score_chunk_bonus(chunk, document, query)
        payloads.append(build_search_payload(chunk, document, query, score, {"dense": dense_score}))
    if payloads and dense_scores and max(abs(score) for score in dense_scores) <= 1e-12:
        if model_audit is not None:
            model_audit["vector_index_warning"] = "qdrant_returned_only_zero_scores"
        return []
    payloads.sort(key=lambda item: item["score"], reverse=True)
    return attach_model_audit(payloads[:top_k], model_audit) if model_audit is not None else payloads[:top_k]


async def search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    results, _audit = await hybrid_search_chunks_with_audit(db, course_id, query, filters, top_k)
    return results


async def search_chunks_with_audit(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> tuple[list[dict], dict]:
    return await hybrid_search_chunks_with_audit(db, course_id, query, filters, top_k)


async def hybrid_search_chunks(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> list[dict]:
    results, _audit = await hybrid_search_chunks_with_audit(db, course_id, query, filters, top_k)
    return results


async def hybrid_search_chunks_with_audit(db: Session, course_id: str, query: str, filters: SearchFilters, top_k: int) -> tuple[list[dict], dict]:
    settings = get_settings()
    config = query_type_config(query)
    recall_k = max(int(config["recall_k"]), top_k)
    dense_results: list[dict] = []
    model_audit = default_model_audit()
    if is_degraded_mode() and not settings.enable_model_fallback:
        raise RuntimeError("OPENAI_API_KEY is required for search because ENABLE_MODEL_FALLBACK is false")
    if not is_degraded_mode():
        try:
            dense_results = await dense_search_chunks(db, course_id, query, filters, recall_k, model_audit)
        except Exception:
            if not settings.enable_model_fallback:
                raise
            model_audit["embedding_fallback_reason"] = "dense_embedding_failed"
            dense_results = []
    lexical_results = lexical_search_chunks(db, course_id, query, filters, recall_k)
    if not dense_results:
        results = rerank_or_return(query, lexical_results, top_k) if lexical_results else []
        model_audit["reranker_called"] = did_rerank(results)
        results = expand_results_with_parent_context(db, course_id, results)
        return attach_model_audit(results, model_audit), model_audit
    if not lexical_results:
        results = rerank_or_return(query, dense_results, top_k)
        model_audit["reranker_called"] = did_rerank(results)
        results = expand_results_with_parent_context(db, course_id, results)
        return attach_model_audit(results, model_audit), model_audit

    candidates = weighted_score_fusion(
        dense_results,
        lexical_results,
        alpha=float(config["alpha"]),
        top_n=max(recall_k, top_k),
    )
    for item in candidates:
        item.setdefault("metadata", {}).setdefault("scores", {})["query_type"] = config["query_type"]
    results = rerank_or_return(query, candidates, top_k)
    model_audit["reranker_called"] = did_rerank(results)
    results = expand_results_with_parent_context(db, course_id, results)
    return attach_model_audit(results, model_audit), model_audit


def lightweight_rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """轻量精排：零外部模型，纯规则 + 统计信号。"""
    if not candidates:
        return []

    query_terms = set(tokenize_for_retrieval(query))
    query_len = len(query_terms) or 1

    scored = []
    for item in candidates:
        haystack = f"{item.get('document_title', '')} {item.get('snippet', '')} {item.get('content', '')}"
        doc_terms = set(tokenize_for_retrieval(haystack))
        overlap = query_terms.intersection(doc_terms)
        overlap_ratio = len(overlap) / query_len

        fused_score = float(item.get("metadata", {}).get("scores", {}).get("fused", item.get("score", 0.0)))

        query_type = item.get("metadata", {}).get("scores", {}).get("query_type", "default")
        alpha = 0.65 if query_type in ("definition", "formula") else 0.75

        final_score = alpha * fused_score + (1.0 - alpha) * overlap_ratio

        scores = item.setdefault("metadata", {}).setdefault("scores", {})
        scores["lightweight_rerank"] = round(final_score, 4)
        scores["term_overlap_ratio"] = round(overlap_ratio, 4)
        scores["rerank"] = round(final_score, 4)
        item["score"] = final_score
        scored.append(item)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def rerank_or_return(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    settings = get_settings()
    if not settings.reranker_enabled:
        return lightweight_rerank(query, candidates, top_k)
    flag = read_env_bool("RERANKER_ENABLED", settings.reranker_enabled)
    if flag:
        reranker = get_reranker()
        return reranker.rerank(query, candidates, top_k)
    return lightweight_rerank(query, candidates, top_k)


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
    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]
    return expand_results_with_parent_context(db, course_id, ranked)


async def graph_enhanced_search_v2(
    db: Session,
    course_id: str,
    query: str,
    filters: SearchFilters,
    top_k: int,
    query_type: str = "default",
) -> list[dict]:
    """Graph-enhanced search with centrality boost, community aggregation, and Dijkstra path expansion."""
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
    seed_concept_ids = {
        concept_id
        for relation in seed_relations
        for concept_id in (relation.source_concept_id, relation.target_concept_id)
        if concept_id
    }
    if not seed_concept_ids:
        return base_results

    concepts = {
        concept.id: concept
        for concept in db.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    }
    seed_concepts = {cid: concepts[cid] for cid in seed_concept_ids if cid in concepts}

    # Determine relation type priority based on query
    relation_priority: set[str] = set()
    if query_type == "comparison":
        relation_priority = {"compares", "relates_to", "contrasts_with"}
    elif query_type == "definition":
        relation_priority = {"defines", "example_of"}
    elif query_type == "procedure":
        relation_priority = {"solves", "extends", "prerequisite_of"}

    neighbor_relations = db.scalars(
        select(ConceptRelation).where(
            ConceptRelation.course_id == course_id,
            or_(
                ConceptRelation.source_concept_id.in_(seed_concept_ids),
                ConceptRelation.target_concept_id.in_(seed_concept_ids),
            ),
        )
    ).all()

    # Centrality and community boost
    boost_by_chunk: dict[str, float] = {}
    boost_by_concept: dict[str, float] = {}
    for relation in neighbor_relations:
        if not relation.evidence_chunk_id:
            continue
        source = concepts.get(relation.source_concept_id)
        target = concepts.get(relation.target_concept_id or "")
        source_imp = float(getattr(source, "importance_score", 0.0) or 0.0)
        target_imp = float(getattr(target, "importance_score", 0.0) or 0.0)
        source_cent = float((getattr(source, "centrality_json", {}) or {}).get("centrality_score", 0.0))
        target_cent = float((getattr(target, "centrality_json", {}) or {}).get("centrality_score", 0.0))
        importance = max(source_imp, target_imp)
        centrality = max(source_cent, target_cent)

        # Relation type boost
        type_boost = 1.3 if relation_priority and relation.relation_type in relation_priority else 1.0

        # Community aggregation: boost if source or target shares community with seed
        community_boost = 1.0
        seed_communities: set[int] = set()
        for sc in seed_concepts.values():
            if sc.community_louvain is not None:
                seed_communities.add(sc.community_louvain)
        if source and source.community_louvain in seed_communities:
            community_boost = 1.15
        if target and target.community_louvain in seed_communities:
            community_boost = 1.15

        boost = float(relation.confidence or 0.0) * importance * (1.0 + centrality) * type_boost * community_boost
        boost_by_chunk[relation.evidence_chunk_id] = max(boost_by_chunk.get(relation.evidence_chunk_id, 0.0), boost)
        for cid in (relation.source_concept_id, relation.target_concept_id):
            if cid:
                boost_by_concept[cid] = max(boost_by_concept.get(cid, 0.0), boost)

    # Dijkstra path expansion for multi-hop (2-3 hops between seed concepts)
    try:
        import networkx as nx

        G = nx.Graph()
        all_relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()
        for rel in all_relations:
            if rel.source_concept_id and rel.target_concept_id:
                w = float(rel.weight or rel.confidence or 0.1)
                G.add_edge(rel.source_concept_id, rel.target_concept_id, weight=1.0 / (0.05 + w))
        for source_id in seed_concept_ids:
            for target_id in seed_concept_ids:
                if source_id >= target_id:
                    continue
                if nx.has_path(G, source_id, target_id):
                    try:
                        path = nx.shortest_path(G, source_id, target_id, weight="weight")
                        if 3 <= len(path) <= 4:  # 2-3 hops
                            path_boost = max(boost_by_concept.get(source_id, 0.5), boost_by_concept.get(target_id, 0.5)) * 0.6
                            for node_id in path[1:-1]:
                                # Add evidence chunks from path intermediates
                                node_rels = db.scalars(
                                    select(ConceptRelation).where(
                                        ConceptRelation.course_id == course_id,
                                        or_(
                                            ConceptRelation.source_concept_id == node_id,
                                            ConceptRelation.target_concept_id == node_id,
                                        ),
                                    )
                                ).all()
                                for nr in node_rels:
                                    if nr.evidence_chunk_id and nr.evidence_chunk_id not in base_chunk_ids:
                                        boost_by_chunk[nr.evidence_chunk_id] = max(
                                            boost_by_chunk.get(nr.evidence_chunk_id, 0.0),
                                            path_boost,
                                        )
                    except (nx.NetworkXNoPath, nx.NodeNotFound):
                        pass
    except Exception:
        pass

    evidence_ids = {cid for cid in boost_by_chunk if cid not in base_chunk_ids}
    if evidence_ids:
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
            item = build_search_payload(chunk, document, query, graph_boost, {"graph_boost": graph_boost, "graph_expanded": True})
            merged[chunk.id] = item

    for item in merged.values():
        scores = item.setdefault("metadata", {}).setdefault("scores", {})
        if "graph_boost" in scores:
            item["score"] = float(item["score"]) + float(scores["graph_boost"])
    ranked = sorted(merged.values(), key=lambda item: item["score"], reverse=True)[:top_k]
    return expand_results_with_parent_context(db, course_id, ranked)


def select_retrieval_layer(query_type: str, route: str) -> int:
    return RETRIEVAL_LAYER_CONFIG.get((query_type, route), 2)


async def layered_search_chunks(
    db: Session,
    course_id: str,
    query: str,
    filters: SearchFilters,
    top_k: int,
    route: str = "retrieve_notes",
    use_cache: bool = True,
) -> tuple[list[dict], dict]:
    """Layered retrieval entry point with caching and query-type-aware depth selection."""
    config = query_type_config(query)
    query_type = config["query_type"]
    layer = select_retrieval_layer(query_type, route)
    cache = get_cache_manager()
    filters_hash = f"{filters.chapter or ''}:{filters.source_type or ''}:{','.join(filters.tags or [])}"
    embedding_version = "contextual_enriched_v2"

    if use_cache:
        cached = cache.get_search_results(course_id, query, filters_hash, embedding_version)
        if cached is not None:
            return cached, {"cached": True, "layer": layer}

    if layer == 1:
        # Fast: dense only
        model_audit = default_model_audit()
        results = await dense_search_chunks(db, course_id, query, filters, top_k, model_audit)
        results = expand_results_with_parent_context(db, course_id, results)
        results = attach_model_audit(results, model_audit)
    elif layer == 3:
        # Deep graph: hybrid + graph v2
        results = await graph_enhanced_search_v2(db, course_id, query, filters, top_k, query_type)
    else:
        # Standard hybrid
        results, _ = await hybrid_search_chunks_with_audit(db, course_id, query, filters, top_k)

    if use_cache and results:
        cache.set_search_results(course_id, query, filters_hash, embedding_version, results)
    return results, {"layer": layer, "query_type": query_type}


async def local_graph_search(
    db: Session,
    course_id: str,
    query: str,
    filters: SearchFilters,
    top_k: int,
    seed_concept_ids: list[str],
) -> list[dict]:
    """Retrieve chunks centered around seed concepts and their 1-hop neighbors."""
    from app.models import Concept, ConceptRelation

    # 1. Get seed concept evidence chunks
    seed_chunks = db.scalars(
        select(Chunk)
        .join(ConceptRelation, Chunk.id == ConceptRelation.evidence_chunk_id)
        .where(
            Chunk.course_id == course_id,
            Chunk.is_active.is_(True),
            or_(
                ConceptRelation.source_concept_id.in_(seed_concept_ids),
                ConceptRelation.target_concept_id.in_(seed_concept_ids),
            ),
        )
        .distinct()
    ).all()

    # 2. Get neighbor concept evidence chunks
    neighbor_relations = db.scalars(
        select(ConceptRelation).where(
            ConceptRelation.course_id == course_id,
            or_(
                ConceptRelation.source_concept_id.in_(seed_concept_ids),
                ConceptRelation.target_concept_id.in_(seed_concept_ids),
            ),
        )
    ).all()
    neighbor_concept_ids = {
        rel.target_concept_id if rel.source_concept_id in set(seed_concept_ids) else rel.source_concept_id
        for rel in neighbor_relations
        if rel.target_concept_id and rel.source_concept_id
    }
    neighbor_chunks = []
    if neighbor_concept_ids:
        neighbor_chunks = db.scalars(
            select(Chunk)
            .join(ConceptRelation, Chunk.id == ConceptRelation.evidence_chunk_id)
            .where(
                Chunk.course_id == course_id,
                Chunk.is_active.is_(True),
                or_(
                    ConceptRelation.source_concept_id.in_(list(neighbor_concept_ids)),
                    ConceptRelation.target_concept_id.in_(list(neighbor_concept_ids)),
                ),
            )
            .distinct()
        ).all()

    # 3. Also do a base dense recall and merge
    base_results, _ = await hybrid_search_chunks_with_audit(db, course_id, query, filters, top_k)
    base_ids = {r["chunk_id"] for r in base_results}

    # 4. Build result set: seed chunks get highest score, neighbor next, base dense fills remaining
    result_map: dict[str, dict] = {}
    for chunk in seed_chunks:
        if not is_child_retrieval_candidate(chunk, db):
            continue
        result_map[str(chunk.id)] = chunk_to_result(chunk, score=1.0, source="local_graph_seed")
    for chunk in neighbor_chunks:
        if not is_child_retrieval_candidate(chunk, db):
            continue
        cid = str(chunk.id)
        if cid not in result_map:
            result_map[cid] = chunk_to_result(chunk, score=0.8, source="local_graph_neighbor")

    # Add base dense results with lower priority
    for result in base_results:
        cid = result["chunk_id"]
        if cid not in result_map:
            result_map[cid] = {**result, "source": "local_graph_dense"}
        else:
            # Boost if also in dense results
            existing = result_map[cid]
            existing["score"] = max(existing.get("score", 0.0), result.get("score", 0.0)) + 0.1

    results = sorted(result_map.values(), key=lambda x: x.get("score", 0.0), reverse=True)[:top_k]
    return expand_results_with_parent_context(db, course_id, results)


async def community_search_chunks(
    db: Session,
    course_id: str,
    query: str,
    filters: SearchFilters,
    top_k: int,
    community_ids: list[int],
) -> list[dict]:
    """Retrieve chunks from concepts within specified communities, blended with dense recall."""
    from app.models import Concept

    community_concepts = db.scalars(
        select(Concept).where(
            Concept.course_id == course_id,
            Concept.community_louvain.in_(community_ids),
        )
    ).all()
    concept_ids = {c.id for c in community_concepts}

    # Get evidence chunks from community concepts
    community_chunk_ids: set[str] = set()
    if concept_ids:
        from app.models import ConceptRelation
        relations = db.scalars(
            select(ConceptRelation).where(
                ConceptRelation.course_id == course_id,
                or_(
                    ConceptRelation.source_concept_id.in_(concept_ids),
                    ConceptRelation.target_concept_id.in_(concept_ids),
                ),
            )
        ).all()
        community_chunk_ids = {str(r.evidence_chunk_id) for r in relations if r.evidence_chunk_id}

    # Base dense recall
    base_results, _ = await hybrid_search_chunks_with_audit(db, course_id, query, filters, top_k)

    # Boost community chunks
    result_map: dict[str, dict] = {}
    for result in base_results:
        cid = result["chunk_id"]
        score = result.get("score", 0.0)
        if cid in community_chunk_ids:
            score += 0.15
        result_map[cid] = {**result, "score": score, "source": "community" if cid in community_chunk_ids else "dense"}

    results = sorted(result_map.values(), key=lambda x: x.get("score", 0.0), reverse=True)[:top_k]
    return expand_results_with_parent_context(db, course_id, results)


def chunk_to_result(chunk, score: float = 0.0, source: str = "") -> dict:
    from app.models import Document
    document = chunk.document if hasattr(chunk, "document") and chunk.document else None
    return {
        "chunk_id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "document_title": getattr(document, "title", "") if document else "",
        "source_path": getattr(document, "source_path", "") if document else "",
        "chapter": chunk.chapter or "General",
        "section": chunk.section,
        "page_number": chunk.page_number,
        "snippet": chunk.snippet or "",
        "content": chunk.content or "",
        "source_type": chunk.source_type or "",
        "citations": [{
            "chunk_id": str(chunk.id),
            "document_id": str(chunk.document_id),
            "document_title": getattr(document, "title", "") if document else "",
            "source_path": getattr(document, "source_path", "") if document else "",
            "chapter": chunk.chapter or "General",
            "section": chunk.section,
            "page_number": chunk.page_number,
            "snippet": chunk.snippet or "",
        }],
        "metadata": {"scores": {"dense": score}, "source": source},
        "score": score,
    }


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


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
        if not is_child_retrieval_candidate(chunk, db):
            continue
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
