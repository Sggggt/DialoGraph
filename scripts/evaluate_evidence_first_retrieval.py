#!/usr/bin/env python3
"""Compare base-only vs evidence-first retrieval proxies.

Run inside the API container:
    python /app/scripts/evaluate_evidence_first_retrieval.py --course-name "Course Name"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.db import SessionLocal, ensure_schema
from app.models import Concept, ConceptRelation, Course
from app.schemas import SearchFilters
from app.services.retrieval import evidence_first_search_chunks_with_audit, hybrid_search_chunks_with_audit


def build_queries(db, course_id: str, limit: int) -> list[str]:
    concepts = db.scalars(
        select(Concept)
        .where(Concept.course_id == course_id, Concept.evidence_count > 0)
        .order_by(Concept.graph_rank_score.desc(), Concept.evidence_count.desc())
        .limit(limit)
    ).all()
    queries = [f"Explain {concept.canonical_name}" for concept in concepts[: max(1, limit // 2)]]
    relations = db.scalars(
        select(ConceptRelation)
        .where(ConceptRelation.course_id == course_id, ConceptRelation.evidence_chunk_id.is_not(None))
        .order_by(ConceptRelation.weight.desc(), ConceptRelation.confidence.desc())
        .limit(limit)
    ).all()
    for relation in relations[: max(1, limit - len(queries))]:
        source = db.get(Concept, relation.source_concept_id)
        target = db.get(Concept, relation.target_concept_id) if relation.target_concept_id else None
        source_name = source.canonical_name if source else relation.source_concept_id
        target_name = target.canonical_name if target else relation.target_name
        queries.append(f"How does {source_name} relate to {target_name}?")
    return list(dict.fromkeys(queries))[:limit]


def precision_proxy(results: list[dict]) -> float:
    if not results:
        return 0.0
    accepted = 0
    for item in results:
        metadata = item.get("metadata") or {}
        scores = metadata.get("scores") or {}
        if float(scores.get("rerank", 0.0) or scores.get("fused", 0.0) or item.get("score", 0.0) or 0.0) >= 0.3:
            accepted += 1
    return accepted / len(results)


async def evaluate_course(course: Course, query_limit: int, top_k: int) -> dict:
    rows = []
    with SessionLocal() as db:
        queries = build_queries(db, course.id, query_limit)
    for query in queries:
        filters = SearchFilters()
        with SessionLocal() as db:
            start = time.perf_counter()
            base_results, base_audit = await hybrid_search_chunks_with_audit(db, course.id, query, filters, top_k)
            base_ms = int((time.perf_counter() - start) * 1000)
        with SessionLocal() as db:
            start = time.perf_counter()
            evidence_results, evidence_audit = await evidence_first_search_chunks_with_audit(
                db,
                course.id,
                query,
                filters,
                top_k,
                route="multi_hop_research" if "relate" in query.lower() else "retrieve_notes",
            )
            evidence_ms = int((time.perf_counter() - start) * 1000)
        base_ids = {item["chunk_id"] for item in base_results}
        evidence_ids = {item["chunk_id"] for item in evidence_results}
        graph_chunks = sum(1 for item in evidence_results if (item.get("metadata") or {}).get("evidence_role") in {"path_edge", "community_summary"})
        rows.append(
            {
                "query": query,
                "base_count": len(base_results),
                "evidence_first_count": len(evidence_results),
                "base_precision_proxy": round(precision_proxy(base_results), 4),
                "evidence_precision_proxy": round(precision_proxy(evidence_results), 4),
                "recall_proxy_overlap": round(len(base_ids.intersection(evidence_ids)) / max(len(base_ids), 1), 4),
                "graph_expansion_ratio": round(graph_chunks / max(len(evidence_results), 1), 4),
                "base_latency_ms": base_ms,
                "evidence_first_latency_ms": evidence_ms,
                "audit": evidence_audit,
                "base_audit": base_audit,
            }
        )
    return {
        "course_id": course.id,
        "course_name": course.name,
        "query_count": len(rows),
        "summary": {
            "base_latency_ms_avg": round(statistics.mean([row["base_latency_ms"] for row in rows]), 2) if rows else 0,
            "evidence_first_latency_ms_avg": round(statistics.mean([row["evidence_first_latency_ms"] for row in rows]), 2) if rows else 0,
            "base_precision_proxy_avg": round(statistics.mean([row["base_precision_proxy"] for row in rows]), 4) if rows else 0,
            "evidence_precision_proxy_avg": round(statistics.mean([row["evidence_precision_proxy"] for row in rows]), 4) if rows else 0,
            "graph_expansion_ratio_avg": round(statistics.mean([row["graph_expansion_ratio"] for row in rows]), 4) if rows else 0,
        },
        "queries": rows,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate evidence-first retrieval proxies.")
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--query-limit", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    ensure_schema()
    with SessionLocal() as db:
        query = select(Course)
        if args.course_id:
            query = query.where(Course.id == args.course_id)
        if args.course_name:
            query = query.where(Course.name == args.course_name)
        courses = db.scalars(query.order_by(Course.name.asc())).all()
    if not courses:
        raise SystemExit("No matching courses found.")

    output = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "top_k": args.top_k,
        "courses": [await evaluate_course(course, args.query_limit, args.top_k) for course in courses],
    }
    output_dir = Path(__file__).resolve().parents[1] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"evidence_first_retrieval_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "courses": len(output["courses"])}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
