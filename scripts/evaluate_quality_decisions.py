#!/usr/bin/env python3
"""Export quality decision distributions and review samples.

Run inside the API container:
    python /app/scripts/evaluate_quality_decisions.py --course-name "Course Name"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.db import SessionLocal, ensure_schema
from app.models import Chunk, Concept, ConceptRelation, Course, GraphRelationCandidate, QualityProfile


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _first_reason(decision: dict[str, Any]) -> str:
    reasons = decision.get("reasons")
    if isinstance(reasons, list) and reasons:
        return str(reasons[0])
    return "missing_reason"


def _sample(item_type: str, item_id: str, text: str, decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": item_type,
        "id": item_id,
        "text": text[:500],
        "action": decision.get("action"),
        "score": decision.get("score"),
        "reasons": decision.get("reasons", []),
    }


def build_course_report(db, course: Course, sample_limit: int) -> dict[str, Any]:
    chunks = db.scalars(select(Chunk).where(Chunk.course_id == course.id, Chunk.is_active.is_(True))).all()
    concepts = db.scalars(select(Concept).where(Concept.course_id == course.id)).all()
    relations = db.scalars(select(ConceptRelation).where(ConceptRelation.course_id == course.id)).all()
    candidates = db.scalars(select(GraphRelationCandidate).where(GraphRelationCandidate.course_id == course.id)).all()
    profile = db.scalar(
        select(QualityProfile)
        .where(QualityProfile.course_id == course.id, QualityProfile.is_active.is_(True))
        .order_by(QualityProfile.created_at.desc())
    )

    chunk_actions: Counter[str] = Counter()
    chunk_reasons: Counter[str] = Counter()
    chunk_retention: Counter[str] = Counter()
    chunk_routes: Counter[str] = Counter()
    concept_actions: Counter[str] = Counter()
    concept_reasons: Counter[str] = Counter()
    relation_actions: Counter[str] = Counter()
    relation_reasons: Counter[str] = Counter()
    candidate_actions: Counter[str] = Counter()
    candidate_reasons: Counter[str] = Counter()
    review_samples: list[dict[str, Any]] = []

    for chunk in chunks:
        metadata = chunk.metadata_json or {}
        action = str(metadata.get("quality_action") or "missing")
        chunk_actions[action] += 1
        if metadata.get("quality_retain") is False:
            chunk_retention["discarded"] += 1
        elif "quality_retain" in metadata:
            chunk_retention["retained"] += 1
        else:
            chunk_retention["missing"] += 1
        routes = metadata.get("route_eligibility")
        if isinstance(routes, dict):
            for route_name in ("embed", "retrieval", "graph_extraction", "summary", "evidence_only"):
                if routes.get(route_name):
                    chunk_routes[route_name] += 1
        else:
            chunk_routes["missing"] += 1
        for reason in metadata.get("quality_reasons") or ["missing_reason"]:
            chunk_reasons[str(reason)] += 1
        if action in {"discard", "embed_only", "evidence_only", "summary_only"} and len(review_samples) < sample_limit:
            review_samples.append(
                _sample(
                    "chunk",
                    chunk.id,
                    chunk.snippet or chunk.content or "",
                    {"action": action, "score": metadata.get("quality_score"), "reasons": metadata.get("quality_reasons", [])},
                )
            )

    for concept in concepts:
        audit = (concept.quality_json or {}).get("concept_gate") or {}
        action = "accept" if audit.get("gate_reason") in {None, "policy_passed", "existing_concept"} else "reject_or_pruned"
        concept_actions[action] += 1
        concept_reasons[str(audit.get("gate_reason") or "missing_reason")] += 1
        if action != "accept" and len(review_samples) < sample_limit:
            review_samples.append(_sample("concept", concept.id, concept.canonical_name, {"action": action, "score": audit.get("specificity_score"), "reasons": [audit.get("gate_reason")]}))

    for relation in relations:
        decision = ((relation.metadata_json or {}).get("quality_decision") or {})
        action = str(decision.get("action") or "missing")
        relation_actions[action] += 1
        relation_reasons[_first_reason(decision)] += 1

    for candidate in candidates:
        decision = candidate.decision_json or {}
        action = str(decision.get("action") or "candidate_only")
        candidate_actions[action] += 1
        candidate_reasons[_first_reason(decision)] += 1
        if len(review_samples) < sample_limit:
            review_samples.append(
                _sample(
                    "relation_candidate",
                    candidate.id,
                    f"{candidate.source_concept_id} {candidate.relation_type} {candidate.target_name}",
                    decision,
                )
            )

    accepted_relations = len(relations)
    candidate_relations = len(candidates)
    return {
        "course_id": course.id,
        "course_name": course.name,
        "quality_profile_version": profile.version if profile else None,
        "counts": {
            "chunks": len(chunks),
            "concepts": len(concepts),
            "accepted_relations": accepted_relations,
            "relation_candidates": candidate_relations,
            "candidate_to_accepted_relation_ratio": round(candidate_relations / max(accepted_relations, 1), 4),
        },
        "distributions": {
            "chunk_actions": _counter_dict(chunk_actions),
            "chunk_reasons": _counter_dict(chunk_reasons),
            "chunk_retention": _counter_dict(chunk_retention),
            "chunk_routes": _counter_dict(chunk_routes),
            "concept_actions": _counter_dict(concept_actions),
            "concept_reasons": _counter_dict(concept_reasons),
            "relation_actions": _counter_dict(relation_actions),
            "relation_reasons": _counter_dict(relation_reasons),
            "candidate_actions": _counter_dict(candidate_actions),
            "candidate_reasons": _counter_dict(candidate_reasons),
        },
        "active_learning_samples": review_samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export quality decision audit report.")
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--sample-limit", type=int, default=50)
    parser.add_argument("--output-dir", default="output")
    args = parser.parse_args()

    ensure_schema()
    with SessionLocal() as db:
        course_query = select(Course)
        if args.course_id:
            course_query = course_query.where(Course.id == args.course_id)
        if args.course_name:
            course_query = course_query.where(Course.name == args.course_name)
        courses = db.scalars(course_query.order_by(Course.name.asc())).all()
        if not courses:
            raise SystemExit("No matching courses found.")
        report = {
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "courses": [build_course_report(db, course, args.sample_limit) for course in courses],
        }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"quality_decisions_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
