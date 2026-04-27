from __future__ import annotations

import re
import asyncio
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, Concept, ConceptAlias, ConceptRelation, Course, Document
from app.schemas import Citation
from app.services.embeddings import ChatProvider
from app.services.ingestion_logs import emit_ingestion_log
from app.services.parsers import canonical_chapter_label, derive_chapter, is_invalid_chapter_label


ALLOWED_RELATIONS = {
    "defines",
    "relates_to",
    "prerequisite_of",
    "example_of",
    "solves",
    "compares",
    "extends",
    "mentions",
}
GRAPH_EXTRACTION_CONCURRENCY = 2
KEYWORD_TERMS = [
    "eigenvector centrality",
    "betweenness centrality",
    "harmonic centrality",
    "closeness centrality",
    "adjacency matrix",
    "degree matrix",
    "laplacian matrix",
    "algebraic connectivity",
    "bipartite network",
    "random network",
    "community detection",
    "modularity",
    "percolation",
    "clustering coefficient",
    "connected component",
    "pagerank",
    "spectral graph theory",
]
GRAPH_TOPIC_HINTS = [
    "breadth-first search",
    "depth-first search",
    "dijkstra",
    "bellman-ford",
    "floyd-warshall",
    "kruskal",
    "prim",
    "minimum spanning tree",
    "spanning tree",
    "ford-fulkerson",
    "max-flow",
    "maximum flow",
    "flow network",
    "matching",
    "vertex cover",
    "independent set",
    "coloring",
    "planar graph",
    "eulerian",
    "hamiltonian",
    "np-complete",
    "np-hard",
    "complexity class",
    "tree search",
    "shortest path",
    "cut",
    "connectivity",
    "matrix tree",
]
STOP_CONCEPTS = {
    "the",
    "this",
    "that",
    "these",
    "those",
    "proof",
    "answer",
    "answers",
    "exercise",
    "code cell",
    "output",
    "question",
    "graph",
    "networkx",
    "python",
    "analysis",
    "homework",
    "solution",
    "solutions",
    "notebook",
    "what",
    "you",
    "your",
    "please",
    "include",
    "instructions",
    "instruction",
    "task",
    "edit",
    "code",
    "data",
    "tip",
    "when",
    "remember",
    "submission",
    "evaluation",
    "april",
    "for",
}
CONCEPT_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9\-]+(?:\s+[A-Z][A-Za-z0-9\-]+){0,4}\b")


def graph_extraction_provider() -> str:
    settings = get_settings()
    if settings.openai_api_key:
        return "openai_compatible_chat"
    return "heuristic" if settings.enable_model_fallback else "unavailable"


def normalize_concept_name(name: str) -> str:
    value = re.sub(r"\$[^$]*\$", " ", name)
    value = re.sub(r"[_*#`~\[\]\(\)]", " ", value)
    value = re.sub(r"[^0-9A-Za-z+\-/\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    if value.endswith("ies") and len(value) > 4:
        value = value[:-3] + "y"
    elif value.endswith("s") and not value.endswith("ss") and len(value) > 4:
        value = value[:-1]
    return value


def clean_display_name(name: str) -> str:
    raw = re.sub(r"\s+", " ", name).strip()
    if raw.isupper():
        return raw
    return raw[:1].upper() + raw[1:]


def is_valid_concept(name: str) -> bool:
    normalized = normalize_concept_name(name)
    if not normalized or len(normalized) < 3:
        return False
    if normalized in STOP_CONCEPTS:
        return False
    if any(token in normalized for token in {"networkx", "analysis", "analysi", "homework", "solution", "notebook"}):
        return False
    if any(char in name for char in "()[]{}_=<>"):
        return False
    if normalized.startswith("figure ") or normalized.startswith("table "):
        return False
    return True


def heuristic_extract_graph(text: str) -> dict:
    candidates: dict[str, dict] = {}
    for term in KEYWORD_TERMS:
        if term in text.lower():
            display = clean_display_name(term.title())
            candidates[normalize_concept_name(display)] = {
                "name": display,
                "aliases": [display],
                "summary": "",
                "concept_type": "concept",
                "importance_score": 0.72,
            }
    for match in CONCEPT_PATTERN.findall(text):
        if not is_valid_concept(match):
            continue
        display = clean_display_name(match)
        candidates.setdefault(
            normalize_concept_name(display),
            {
                "name": display,
                "aliases": [display],
                "summary": "",
                "concept_type": "concept",
                "importance_score": 0.55,
            },
        )

    names = list(candidates.values())
    relations = []
    for left, right in zip(names, names[1:]):
        relations.append(
            {
                "source": left["name"],
                "target": right["name"],
                "relation_type": "mentions",
                "confidence": 0.4,
            }
        )
    return {"concepts": list(candidates.values())[:12], "relations": relations[:18]}


def merge_graph_candidates(primary: dict, fallback: dict) -> dict:
    merged_concepts: dict[str, dict] = {}
    for source in (fallback, primary):
        for concept in source.get("concepts", []):
            name = concept.get("name", "")
            if not is_valid_concept(name):
                continue
            normalized = normalize_concept_name(name)
            current = merged_concepts.get(normalized, {})
            aliases = {alias for alias in current.get("aliases", []) if alias} | {
                alias for alias in concept.get("aliases", []) if alias
            }
            if not aliases:
                aliases = {clean_display_name(name)}
            merged_concepts[normalized] = {
                "name": current.get("name") or clean_display_name(name),
                "aliases": sorted(aliases),
                "summary": concept.get("summary") or current.get("summary", ""),
                "concept_type": concept.get("concept_type") or current.get("concept_type", "concept"),
                "importance_score": max(float(current.get("importance_score", 0.0)), float(concept.get("importance_score", 0.0))),
            }

    relations = []
    seen_relations: set[tuple[str, str, str]] = set()
    for source in (fallback, primary):
        for relation in source.get("relations", []):
            relation_type = relation.get("relation_type", "mentions")
            if relation_type not in ALLOWED_RELATIONS:
                continue
            source_name = relation.get("source", "")
            target_name = relation.get("target", "")
            if not is_valid_concept(source_name) or not is_valid_concept(target_name):
                continue
            key = (
                normalize_concept_name(source_name),
                normalize_concept_name(target_name),
                relation_type,
            )
            if key in seen_relations:
                continue
            seen_relations.add(key)
            relations.append(
                {
                    "source": clean_display_name(source_name),
                    "target": clean_display_name(target_name),
                    "relation_type": relation_type,
                    "confidence": float(relation.get("confidence", 0.5)),
                }
            )
    return {"concepts": list(merged_concepts.values()), "relations": relations}


def get_or_create_concept(
    db: Session,
    course_id: str,
    name: str,
    chapter: str | None,
    summary: str,
    aliases: list[str],
    concept_type: str,
    importance_score: float,
) -> tuple[Concept, bool]:
    normalized = normalize_concept_name(name)
    chapter_ref = None if is_invalid_chapter_label(chapter) else chapter
    alias_match = db.scalar(select(ConceptAlias).where(ConceptAlias.normalized_alias == normalized))
    concept = None
    if alias_match and alias_match.concept and alias_match.concept.course_id == course_id:
        concept = alias_match.concept
    if concept is None:
        concept = db.scalar(select(Concept).where(Concept.course_id == course_id, Concept.normalized_name == normalized))

    created = False
    if concept is None:
        concept = Concept(
            course_id=course_id,
            canonical_name=clean_display_name(name),
            normalized_name=normalized,
            summary=summary[:800],
            concept_type=concept_type or "concept",
            importance_score=importance_score,
            chapter_refs=[chapter_ref] if chapter_ref else [],
        )
        db.add(concept)
        db.flush()
        created = True
    else:
        if summary and len(summary) > len(concept.summary or ""):
            concept.summary = summary[:800]
        concept.importance_score = max(float(concept.importance_score or 0.0), float(importance_score or 0.0))
        if concept_type and concept.concept_type == "concept":
            concept.concept_type = concept_type
        if chapter_ref and chapter_ref not in concept.chapter_refs:
            concept.chapter_refs = sorted({*concept.chapter_refs, chapter_ref})

    all_aliases = {clean_display_name(concept.canonical_name), *[clean_display_name(alias) for alias in aliases if is_valid_concept(alias)]}
    for alias in all_aliases:
        normalized_alias = normalize_concept_name(alias)
        exists = db.scalar(
            select(ConceptAlias).where(
                ConceptAlias.concept_id == concept.id,
                ConceptAlias.normalized_alias == normalized_alias,
            )
        )
        if exists is None:
            db.add(ConceptAlias(concept_id=concept.id, alias=alias, normalized_alias=normalized_alias))
    return concept, created


async def upsert_concepts_from_chunk(
    db: Session,
    course_id: str,
    chunk: Chunk,
    use_llm: bool = True,
    llm_payload: dict | None = None,
) -> tuple[int, int]:
    settings = get_settings()
    fallback = heuristic_extract_graph(chunk.content) if settings.enable_model_fallback else {"concepts": [], "relations": []}
    if use_llm:
        llm_payload = llm_payload if llm_payload is not None else await ChatProvider().extract_graph_payload(chunk.content, chunk.chapter, chunk.source_type)
    else:
        llm_payload = {"concepts": [], "relations": []}
    extracted = merge_graph_candidates(llm_payload, fallback)
    extraction_method = "llm+rules" if settings.enable_model_fallback and llm_payload.get("concepts") else "llm" if llm_payload.get("concepts") else "heuristic"

    concept_map: dict[str, Concept] = {}
    created_count = 0
    for concept_data in extracted["concepts"]:
        concept, created = get_or_create_concept(
            db=db,
            course_id=course_id,
            name=concept_data["name"],
            chapter=chunk.chapter,
            summary=concept_data.get("summary") or chunk.snippet,
            aliases=concept_data.get("aliases", []),
            concept_type=concept_data.get("concept_type") or "concept",
            importance_score=float(concept_data.get("importance_score", 0.5)),
        )
        concept_map[normalize_concept_name(concept_data["name"])] = concept
        if created:
            created_count += 1

    relation_count = 0
    relation_keys: set[tuple[str, str, str]] = set()
    for relation_data in extracted["relations"]:
        source = concept_map.get(normalize_concept_name(relation_data["source"]))
        target = concept_map.get(normalize_concept_name(relation_data["target"]))
        if source is None or target is None or source.id == target.id:
            continue
        relation_key = (source.id, target.id, relation_data["relation_type"])
        if relation_key in relation_keys:
            continue
        relation_keys.add(relation_key)
        confidence = float(relation_data.get("confidence", 0.55))
        existing = db.scalar(
            select(ConceptRelation).where(
                ConceptRelation.course_id == course_id,
                ConceptRelation.source_concept_id == source.id,
                ConceptRelation.target_concept_id == target.id,
                ConceptRelation.relation_type == relation_data["relation_type"],
            )
        )
        if existing:
            existing.confidence = max(existing.confidence, confidence)
            if confidence >= (existing.confidence or 0):
                existing.evidence_chunk_id = chunk.id
            existing.extraction_method = "llm+rules"
            continue
        db.add(
            ConceptRelation(
                course_id=course_id,
                source_concept_id=source.id,
                target_concept_id=target.id,
                target_name=target.canonical_name,
                relation_type=relation_data["relation_type"],
                evidence_chunk_id=chunk.id,
                confidence=confidence,
                extraction_method=extraction_method,
                is_validated=confidence >= 0.82,
            )
        )
        relation_count += 1
    return created_count, relation_count


def graph_topic_score(chunk: Chunk) -> int:
    haystack = f"{getattr(chunk, 'section', '') or ''}\n{getattr(chunk, 'snippet', '') or ''}\n{chunk.content[:1200]}".lower()
    return sum(1 for hint in GRAPH_TOPIC_HINTS if hint in haystack)


def graph_chunk_rank(chunk: Chunk) -> tuple[int, int, int, int]:
    priority = {"markdown": 4, "pdf_page": 4, "doc_section": 4, "slide": 4, "text": 3, "html": 3, "ocr": 3, "code": 0, "output": 0}
    return (
        graph_topic_score(chunk),
        priority.get((chunk.metadata_json or {}).get("content_kind", "text"), 2),
        len(chunk.content),
        1 if chunk.source_type == "notebook" else 2,
    )


def choose_llm_graph_chunks(
    chunks: list[Chunk],
    limit: int | None = None,
    chunks_per_document: int | None = None,
) -> set[str]:
    settings = get_settings()
    limit = limit or settings.graph_extraction_chunk_limit
    chunks_per_document = chunks_per_document or settings.graph_extraction_chunks_per_document

    ranked = sorted(chunks, key=graph_chunk_rank, reverse=True)
    by_document: dict[str, list[Chunk]] = defaultdict(list)
    for chunk in ranked:
        by_document[chunk.document_id].append(chunk)

    selected: list[Chunk] = []
    selected_ids: set[str] = set()
    representatives = sorted(
        (document_chunks[:chunks_per_document] for document_chunks in by_document.values()),
        key=lambda document_chunks: graph_chunk_rank(document_chunks[0]) if document_chunks else (0, 0, 0),
        reverse=True,
    )
    for document_chunks in representatives:
        for chunk in document_chunks:
            if len(selected) >= limit:
                break
            selected.append(chunk)
            selected_ids.add(chunk.id)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for chunk in ranked:
            if chunk.id in selected_ids:
                continue
            selected.append(chunk)
            selected_ids.add(chunk.id)
            if len(selected) >= limit:
                break
    return selected_ids


async def extract_llm_graph_payloads(
    chunks: list[Chunk],
    concurrency: int = GRAPH_EXTRACTION_CONCURRENCY,
    batch_id: str | None = None,
) -> tuple[dict[str, dict], dict[str, str]]:
    provider = ChatProvider()
    semaphore = asyncio.Semaphore(concurrency)
    total = len(chunks)
    completed = 0
    payloads: dict[str, dict] = {}
    errors: dict[str, str] = {}

    async def extract(chunk: Chunk) -> None:
        nonlocal completed
        async with semaphore:
            try:
                payloads[chunk.id] = await provider.extract_graph_payload(chunk.content, chunk.chapter, chunk.source_type)
            except Exception as exc:
                errors[chunk.id] = f"{type(exc).__name__}: {exc}"
            finally:
                completed += 1
                if batch_id and (completed == total or completed % 5 == 0 or chunk.id in errors):
                    emit_ingestion_log(
                        batch_id,
                        "batch_graph_progress",
                        f"Graph extraction {completed}/{total} chunks",
                        completed_graph_chunks=completed,
                        total_graph_chunks=total,
                        successful_extractions=len(payloads),
                        failed_extractions=len(errors),
                    )

    if not chunks:
        return {}, {}
    await asyncio.gather(*(extract(chunk) for chunk in chunks))
    return payloads, errors


async def rebuild_course_graph(db: Session, course_id: str, batch_id: str | None = None) -> dict:
    settings = get_settings()
    course = db.get(Course, course_id)
    sync_graph_chapter_labels(db, course_id)
    active_documents = db.scalars(select(Document).where(Document.course_id == course_id, Document.is_active.is_(True))).all()
    graph_documents = filter_graph_documents(course, active_documents)
    graph_document_ids = {document.id for document in graph_documents}
    document_chapters = {document.id: document_chapter_label(document, course.name if course else None) for document in graph_documents}
    chunks = db.scalars(
        select(Chunk)
        .where(Chunk.course_id == course_id, Chunk.is_active.is_(True), Chunk.document_id.in_(graph_document_ids))
        .order_by(Chunk.created_at.asc())
    ).all()
    llm_chunk_ids = choose_llm_graph_chunks(chunks)
    selected_llm_chunks = [chunk for chunk in chunks if chunk.id in llm_chunk_ids]
    if batch_id:
        emit_ingestion_log(
            batch_id,
            "batch_graph_selected",
            f"Selected {len(selected_llm_chunks)} chunks from {len(graph_document_ids)} documents for LLM graph extraction",
            selected_llm_chunks=len(selected_llm_chunks),
            graph_source_documents=len(graph_document_ids),
            total_active_chunks=len(chunks),
        )
    try:
        extraction_result = await extract_llm_graph_payloads(selected_llm_chunks, batch_id=batch_id)
    except TypeError as exc:
        if "unexpected keyword argument 'batch_id'" not in str(exc):
            raise
        extraction_result = await extract_llm_graph_payloads(selected_llm_chunks)
    if isinstance(extraction_result, tuple):
        llm_payloads, llm_errors = extraction_result
    else:
        llm_payloads, llm_errors = extraction_result, {}
    if llm_chunk_ids and not llm_payloads:
        sample_error = next(iter(llm_errors.values()), "no LLM graph payloads were returned")
        raise RuntimeError(f"Graph extraction failed for all selected chunks: {sample_error}")
    llm_document_ids = {chunk.document_id for chunk in chunks if chunk.id in llm_chunk_ids}

    db.query(ConceptRelation).filter(ConceptRelation.course_id == course_id).delete(synchronize_session=False)
    concept_ids = [concept.id for concept in db.scalars(select(Concept).where(Concept.course_id == course_id)).all()]
    if concept_ids:
        db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(concept_ids)).delete(synchronize_session=False)
        db.query(Concept).filter(Concept.id.in_(concept_ids)).delete(synchronize_session=False)

    concept_count = 0
    relation_count = 0
    llm_success_chunks = 0
    for chunk in chunks:
        use_llm = chunk.id in llm_payloads
        created, relations = await upsert_concepts_from_chunk(db, course_id, chunk, use_llm=use_llm, llm_payload=llm_payloads.get(chunk.id))
        if use_llm:
            llm_success_chunks += 1
        concept_count += created
        relation_count += relations
    db.commit()
    graph = get_graph_payload(db, course_id)
    return {
        "graph_rebuilt": True,
        "concepts": concept_count,
        "relations": relation_count,
        "graph_nodes": len(graph.get("nodes", [])),
        "graph_edges": len(graph.get("edges", [])),
        "graph_extraction_provider": graph_extraction_provider(),
        "graph_extraction_chunk_limit": settings.graph_extraction_chunk_limit,
        "graph_extraction_chunks_per_document": settings.graph_extraction_chunks_per_document,
        "graph_llm_selected_chunks": len(llm_chunk_ids),
        "graph_llm_source_documents": len(llm_document_ids),
        "graph_llm_success_chunks": llm_success_chunks,
        "graph_llm_failed_chunks": len(llm_errors),
        "graph_llm_errors": llm_errors,
        "graph_total_active_chunks": len(chunks),
        "graph_source_documents": len(graph_document_ids),
    }


def document_chapter_label(document: Document, course_name: str | None = None) -> str:
    path_label = derive_chapter(Path(document.source_path), course_name=course_name)
    if not is_invalid_chapter_label(path_label, course_name=course_name):
        return path_label
    for tag in document.tags or []:
        if not is_invalid_chapter_label(tag, course_name=course_name):
            return tag
    return document.title[:80] or document.source_type


def _is_under_path(path: Path, root: Path) -> bool:
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        return False
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def filter_graph_documents(course: Course | None, documents: list[Document]) -> list[Document]:
    if course is None:
        return documents
    storage_root = get_settings().course_paths_for_name(course.name)["storage_root"]
    storage_documents = [document for document in documents if _is_under_path(Path(document.source_path), storage_root)]
    return storage_documents or documents


def normalize_chapter_ref(value: str, course_name: str | None = None) -> str | None:
    label = canonical_chapter_label(value, course_name=course_name)
    if label and not is_invalid_chapter_label(label, course_name=course_name):
        return label
    if is_invalid_chapter_label(value, course_name=course_name):
        return None
    return value


def sync_graph_chapter_labels(db: Session, course_id: str) -> dict:
    course = db.get(Course, course_id)
    documents = db.scalars(select(Document).where(Document.course_id == course_id, Document.is_active.is_(True))).all()
    graph_documents = filter_graph_documents(course, documents)
    document_chapters = {document.id: document_chapter_label(document, course.name if course else None) for document in graph_documents}
    updated_documents = 0
    updated_chunks = 0
    updated_concepts = 0
    for document in graph_documents:
        chapter = document_chapters[document.id]
        if document.tags != [chapter]:
            document.tags = [chapter]
            updated_documents += 1
    chunks = db.scalars(
        select(Chunk).where(Chunk.course_id == course_id, Chunk.is_active.is_(True), Chunk.document_id.in_(document_chapters))
    ).all()
    for chunk in chunks:
        chapter = document_chapters.get(chunk.document_id)
        if chapter and chunk.chapter != chapter:
            chunk.chapter = chapter
            updated_chunks += 1
    relation_refs: dict[str, set[str]] = defaultdict(set)
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()
    evidence_chunk_ids = {relation.evidence_chunk_id for relation in relations if relation.evidence_chunk_id}
    evidence_chunks = db.scalars(select(Chunk).where(Chunk.id.in_(evidence_chunk_ids))).all() if evidence_chunk_ids else []
    evidence_documents = {
        document.id: document
        for document in db.scalars(
            select(Document).where(Document.id.in_({chunk.document_id for chunk in evidence_chunks}))
        ).all()
    }
    chunk_chapters = {
        chunk.id: document_chapter_label(evidence_documents[chunk.document_id], course.name if course else None)
        for chunk in evidence_chunks
        if chunk.document_id in evidence_documents
    }
    for relation in relations:
        chapter = chunk_chapters.get(relation.evidence_chunk_id or "")
        if not chapter:
            continue
        relation_refs[relation.source_concept_id].add(chapter)
        if relation.target_concept_id:
            relation_refs[relation.target_concept_id].add(chapter)
    concepts = db.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    for concept in concepts:
        normalized_refs = sorted(
            {
                normalized
                for ref in [*(concept.chapter_refs or []), *relation_refs.get(concept.id, set())]
                for normalized in [normalize_chapter_ref(ref, course.name if course else None)]
                if normalized
            }
        )
        if normalized_refs != (concept.chapter_refs or []):
            concept.chapter_refs = normalized_refs
            updated_concepts += 1
    db.commit()
    return {
        "updated_documents": updated_documents,
        "updated_chunks": updated_chunks,
        "updated_concepts": updated_concepts,
    }


def get_concept_cards(db: Session, course_id: str) -> list[dict]:
    concepts = db.scalars(select(Concept).where(Concept.course_id == course_id).order_by(Concept.importance_score.desc(), Concept.canonical_name)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()
    relation_index: dict[str, list[dict]] = defaultdict(list)
    for relation in relations:
        relation_index[relation.source_concept_id].append(
            {
                "concept_id": relation.target_concept_id or relation.id,
                "relation_type": relation.relation_type,
                "target_name": relation.target_name,
                "confidence": relation.confidence,
            }
        )
    return [
        {
            "concept_id": concept.id,
            "name": concept.canonical_name,
            "aliases": sorted({alias.alias for alias in concept.aliases}),
            "summary": concept.summary or "",
            "chapter_refs": concept.chapter_refs,
            "concept_type": concept.concept_type,
            "importance_score": concept.importance_score,
            "related_concepts": relation_index.get(concept.id, []),
        }
        for concept in concepts
    ]


def build_citation(db: Session, chunk_id: str | None) -> dict | None:
    if not chunk_id:
        return None
    chunk = db.get(Chunk, chunk_id)
    if chunk is None:
        return None
    document = db.get(Document, chunk.document_id)
    if document is None:
        return None
    return Citation(
        chunk_id=chunk.id,
        document_id=document.id,
        document_title=document.title,
        source_path=document.source_path,
        chapter=chunk.chapter,
        section=chunk.section,
        page_number=chunk.page_number,
        snippet=chunk.snippet,
    ).model_dump()


def get_graph_node_detail(db: Session, course_id: str, concept_id: str) -> dict | None:
    concept = db.scalar(select(Concept).where(Concept.course_id == course_id, Concept.id == concept_id))
    if concept is None:
        return None
    relations = db.scalars(
        select(ConceptRelation).where(ConceptRelation.course_id == course_id, ConceptRelation.source_concept_id == concept_id)
    ).all()
    return {
        "concept_id": concept.id,
        "name": concept.canonical_name,
        "normalized_name": concept.normalized_name,
        "summary": concept.summary,
        "aliases": sorted({alias.alias for alias in concept.aliases}),
        "chapter_refs": concept.chapter_refs,
        "concept_type": concept.concept_type,
        "importance_score": concept.importance_score,
        "relations": [
            {
                "relation_id": relation.id,
                "relation_type": relation.relation_type,
                "target_concept_id": relation.target_concept_id,
                "target_name": relation.target_name,
                "confidence": relation.confidence,
                "evidence": build_citation(db, relation.evidence_chunk_id),
            }
            for relation in relations
        ],
    }


def cleanup_graph(db: Session, course_id: str) -> dict:
    concepts = db.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    invalid_ids = {concept.id for concept in concepts if not is_valid_concept(concept.canonical_name)}
    removed_concepts = 0
    removed_relations = 0
    if invalid_ids:
        removed_relations += db.query(ConceptRelation).filter(
            ConceptRelation.course_id == course_id,
            (ConceptRelation.source_concept_id.in_(invalid_ids) | ConceptRelation.target_concept_id.in_(invalid_ids)),
        ).delete(synchronize_session=False)
        removed_concepts += db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(invalid_ids)).delete(synchronize_session=False)
        removed_concepts += db.query(Concept).filter(Concept.id.in_(invalid_ids)).delete(synchronize_session=False)
        db.commit()
    return {"removed_concepts": removed_concepts, "removed_relations": removed_relations}


def get_graph_payload(db: Session, course_id: str, chapter: str | None = None) -> dict:
    course = db.get(Course, course_id)
    documents = db.scalars(
        select(Document).where(Document.course_id == course_id, Document.is_active.is_(True)).order_by(Document.title)
    ).all()
    documents = filter_graph_documents(course, documents)
    if chapter:
        documents = [document for document in documents if chapter == document_chapter_label(document, course.name if course else None)]
    concepts = db.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()

    chapter_names = sorted({document_chapter_label(document, course.name if course else None) for document in documents})

    course_label = course.name if course is not None else "Course Workspace"
    nodes = [{"id": f"course:{course_id}", "name": course_label, "category": "course", "value": 1}]
    edges = []

    for chapter_name in chapter_names:
        nodes.append({"id": f"chapter:{chapter_name}", "name": chapter_name, "category": "chapter", "value": 2, "chapter": chapter_name})
        edges.append({"source": f"course:{course_id}", "target": f"chapter:{chapter_name}", "label": "contains", "category": "structure"})

    for document in documents:
        chapter_name = document_chapter_label(document, course.name if course else None)
        nodes.append(
            {
                "id": document.id,
                "name": document.title,
                "category": "document",
                "value": 2,
                "chapter": chapter_name,
                "source_type": document.source_type,
            }
        )
        edges.append({"source": f"chapter:{chapter_name}", "target": document.id, "label": "contains", "category": "structure"})

    filtered_concepts = [concept for concept in concepts if not chapter or chapter in concept.chapter_refs]
    if not chapter:
        filtered_concepts = concepts
    relation_counts: dict[str, int] = defaultdict(int)
    for relation in relations:
        relation_counts[relation.source_concept_id] += 1
        if relation.target_concept_id:
            relation_counts[relation.target_concept_id] += 1
    filtered_concepts = [
        concept
        for concept in filtered_concepts
        if concept.importance_score >= 0.7 or relation_counts.get(concept.id, 0) >= 2 or len(concept.chapter_refs) >= 2
    ]
    filtered_concepts = sorted(filtered_concepts, key=lambda concept: (concept.importance_score, relation_counts.get(concept.id, 0)), reverse=True)[:260]
    concept_ids = {concept.id for concept in filtered_concepts}
    for concept in filtered_concepts:
        nodes.append(
            {
                "id": concept.id,
                "name": concept.canonical_name,
                "category": "concept",
                "value": max(2.0, concept.importance_score * 12),
                "chapter": concept.chapter_refs[0] if concept.chapter_refs else None,
                "importance_score": concept.importance_score,
            }
        )

    for relation in relations:
        if relation.target_concept_id and relation.source_concept_id in concept_ids and relation.target_concept_id in concept_ids:
            edges.append(
                {
                    "source": relation.source_concept_id,
                    "target": relation.target_concept_id,
                    "label": relation.relation_type,
                    "confidence": relation.confidence,
                    "category": "semantic",
                    "evidence_chunk_id": relation.evidence_chunk_id,
                }
            )
    return {"nodes": nodes, "edges": edges, "focus_chapter": chapter}
