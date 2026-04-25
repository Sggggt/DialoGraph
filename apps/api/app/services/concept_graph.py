from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, Concept, ConceptAlias, ConceptRelation, Course, Document
from app.schemas import Citation
from app.services.embeddings import ChatProvider


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
    return "dashscope_chat" if settings.dashscope_api_key and not settings.enable_fake_chat else "heuristic"


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
            chapter_refs=[chapter] if chapter else [],
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
        if chapter and chapter not in concept.chapter_refs:
            concept.chapter_refs = sorted({*concept.chapter_refs, chapter})

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


async def upsert_concepts_from_chunk(db: Session, course_id: str, chunk: Chunk, use_llm: bool = True) -> tuple[int, int]:
    fallback = heuristic_extract_graph(chunk.content)
    llm_payload = await ChatProvider().extract_graph_payload(chunk.content, chunk.chapter, chunk.source_type) if use_llm else {"concepts": [], "relations": []}
    extracted = merge_graph_candidates(llm_payload, fallback)

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
    for relation_data in extracted["relations"]:
        source = concept_map.get(normalize_concept_name(relation_data["source"]))
        target = concept_map.get(normalize_concept_name(relation_data["target"]))
        if source is None or target is None or source.id == target.id:
            continue
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
                extraction_method="llm+rules" if llm_payload.get("concepts") else "heuristic",
                is_validated=confidence >= 0.82,
            )
        )
        relation_count += 1
    return created_count, relation_count


def choose_llm_graph_chunks(chunks: list[Chunk], limit: int = 3) -> set[str]:
    priority = {"markdown": 4, "pdf_page": 4, "doc_section": 4, "slide": 4, "text": 3, "html": 3, "ocr": 3, "code": 0, "output": 0}
    ranked = sorted(
        chunks,
        key=lambda chunk: (
            priority.get((chunk.metadata_json or {}).get("content_kind", "text"), 2),
            len(chunk.content),
            1 if chunk.source_type == "notebook" else 2,
        ),
        reverse=True,
    )
    return {chunk.id for chunk in ranked[:limit]}


async def rebuild_course_graph(db: Session, course_id: str) -> dict:
    db.query(ConceptRelation).filter(ConceptRelation.course_id == course_id).delete(synchronize_session=False)
    concept_ids = [concept.id for concept in db.scalars(select(Concept).where(Concept.course_id == course_id)).all()]
    if concept_ids:
        db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(concept_ids)).delete(synchronize_session=False)
        db.query(Concept).filter(Concept.id.in_(concept_ids)).delete(synchronize_session=False)
    db.commit()

    chunks = db.scalars(
        select(Chunk)
        .join(Document, Document.id == Chunk.document_id)
        .where(Chunk.course_id == course_id, Chunk.is_active.is_(True), Document.is_active.is_(True))
        .order_by(Chunk.created_at.asc())
    ).all()
    llm_chunk_ids = choose_llm_graph_chunks(chunks, limit=3)
    concept_count = 0
    relation_count = 0
    for chunk in chunks:
        created, relations = await upsert_concepts_from_chunk(db, course_id, chunk, use_llm=chunk.id in llm_chunk_ids)
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
    if chapter:
        documents = [document for document in documents if chapter in (document.tags or [])]
    concepts = db.scalars(select(Concept).where(Concept.course_id == course_id)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course_id)).all()

    chapter_names = sorted({(document.tags[0] if document.tags else document.source_type) for document in documents})

    course_label = course.name if course is not None else "Course Workspace"
    nodes = [{"id": f"course:{course_id}", "name": course_label, "category": "course", "value": 1}]
    edges = []

    for chapter_name in chapter_names:
        nodes.append({"id": f"chapter:{chapter_name}", "name": chapter_name, "category": "chapter", "value": 2, "chapter": chapter_name})
        edges.append({"source": f"course:{course_id}", "target": f"chapter:{chapter_name}", "label": "contains", "category": "structure"})

    for document in documents:
        chapter_name = document.tags[0] if document.tags else document.source_type
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
