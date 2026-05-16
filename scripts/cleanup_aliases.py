#!/usr/bin/env python3
"""Cleanup garbage aliases from concept graph.

Scans all concepts and removes aliases that are semantically unrelated
to the concept's canonical name. Also caps alias count at 50 per concept.

Usage:
    python scripts/cleanup_aliases.py --course-id <uuid> [--dry-run]
    python scripts/cleanup_aliases.py --course-name "Complex Network" [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models import Concept, ConceptAlias


def _is_relevant_alias(alias: str, target_name: str) -> bool:
    """Check whether an alias is semantically related to the target concept name."""
    alias_lower = alias.lower().strip()
    target_lower = target_name.lower().strip()
    if not alias_lower or not target_lower:
        return False
    if alias_lower in target_lower or target_lower in alias_lower:
        return True
    target_tokens = set(target_lower.split())
    alias_tokens = set(alias_lower.split())
    if not target_tokens:
        return False
    overlap = len(target_tokens & alias_tokens)
    return overlap >= max(1, len(target_tokens) * 0.5)


def cleanup_aliases(session: Session, course_id: str | None = None, dry_run: bool = True) -> dict:
    stmt = select(Concept)
    if course_id:
        stmt = stmt.where(Concept.course_id == course_id)

    concepts = session.scalars(stmt).all()
    total_removed = 0
    total_capped = 0
    affected_concepts = 0

    for concept in concepts:
        aliases = list(concept.aliases)
        if not aliases:
            continue

        removed = []
        kept = []
        for alias in aliases:
            if _is_relevant_alias(alias.alias, concept.canonical_name):
                kept.append(alias)
            else:
                removed.append(alias)

        # Cap at 50
        if len(kept) > 50:
            # Keep shortest aliases first (more likely to be core synonyms)
            kept.sort(key=lambda a: (len(a.alias), a.alias))
            removed.extend(kept[50:])
            kept = kept[:50]
            total_capped += len(removed)

        if removed:
            affected_concepts += 1
            total_removed += len(removed)
            print(f"[{concept.canonical_name}] removing {len(removed)} aliases (keeping {len(kept)})")
            for a in removed[:5]:
                print(f"  - {a.alias[:100]}")
            if len(removed) > 5:
                print(f"  ... and {len(removed) - 5} more")

            if not dry_run:
                for a in removed:
                    session.delete(a)

    if not dry_run and total_removed > 0:
        session.commit()

    return {
        "concepts_scanned": len(concepts),
        "affected_concepts": affected_concepts,
        "aliases_removed": total_removed,
        "aliases_capped": total_capped,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--confirm", action="store_true", help="Actually execute deletion")
    args = parser.parse_args()

    # Resolve course_id from name if needed
    course_id = args.course_id
    if args.course_name and not course_id:
        from app.db import SessionLocal
        from app.models import Course
        with SessionLocal() as session:
            course = session.scalars(select(Course).where(Course.name == args.course_name)).first()
            if course is None:
                print(f"Course not found: {args.course_name}")
                sys.exit(1)
            course_id = course.id
            print(f"Resolved course '{args.course_name}' -> {course_id}")

    if not args.confirm:
        print("Running in dry-run mode. Use --confirm to actually delete.")
        args.dry_run = True
    else:
        args.dry_run = False

    from app.db import SessionLocal
    with SessionLocal() as session:
        result = cleanup_aliases(session, course_id=course_id, dry_run=args.dry_run)

    print(f"\nSummary: {result}")


if __name__ == "__main__":
    main()
