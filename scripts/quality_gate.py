#!/usr/bin/env python3
"""No-fallback quality gate for DB/Qdrant chunk-vector health.

Run inside the API container:
    python /app/scripts/quality_gate.py --course-name "Course Name"
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Chunk, Course
from app.services.vector_store import VectorStore


def vector_norm(vector: list[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check active chunk/vector health.")
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--delete-orphan-zero-vectors", action="store_true")
    parser.add_argument("--allow-flat-chunks", action="store_true", help="Do not fail on active chunks without parent/child metadata.")
    args = parser.parse_args()

    settings = get_settings()
    if settings.enable_model_fallback or settings.enable_database_fallback:
        raise SystemExit("Quality gate must run with ENABLE_MODEL_FALLBACK=false and ENABLE_DATABASE_FALLBACK=false.")

    with SessionLocal() as db:
        course_query = select(Course)
        if args.course_id:
            course_query = course_query.where(Course.id == args.course_id)
        if args.course_name:
            course_query = course_query.where(Course.name == args.course_name)
        courses = db.scalars(course_query.order_by(Course.name.asc())).all()
        if not courses:
            raise SystemExit("No matching courses found.")

        ok = True
        for course in courses:
            chunks = db.scalars(select(Chunk).where(Chunk.course_id == course.id, Chunk.is_active.is_(True))).all()
            active_ids = {chunk.id for chunk in chunks}
            parent_count = sum(1 for chunk in chunks if (chunk.metadata_json or {}).get("is_parent") is True)
            explicit_child_count = sum(1 for chunk in chunks if (chunk.metadata_json or {}).get("is_parent") is False)
            legacy_flat_count = len(chunks) - parent_count - explicit_child_count
            child_without_parent = [
                chunk.id
                for chunk in chunks
                if (chunk.metadata_json or {}).get("is_parent") is False and not chunk.parent_chunk_id
            ]

            vector_store = VectorStore(course_name=course.name)
            vector_ids = set(vector_store.list_ids(course.id))
            missing = sorted(active_ids - vector_ids)
            orphan = sorted(vector_ids - active_ids)

            zero_ids: list[str] = []
            checked = 0
            for index in range(0, len(vector_ids), 100):
                batch_ids = sorted(vector_ids)[index : index + 100]
                points = vector_store.get_points(batch_ids)
                checked += len(points)
                for point in points:
                    if vector_norm(point.get("vector") or []) <= 1e-12:
                        zero_ids.append(str(point["id"]))

            orphan_zero_ids = sorted(set(zero_ids).intersection(orphan))
            if args.delete_orphan_zero_vectors and orphan_zero_ids:
                vector_store.delete(orphan_zero_ids)
                orphan = sorted(set(orphan) - set(orphan_zero_ids))
                zero_ids = sorted(set(zero_ids) - set(orphan_zero_ids))

            print(f"\nCourse: {course.name}")
            print(f"  active_chunks={len(chunks)} parent_chunks={parent_count} child_chunks={explicit_child_count} legacy_flat_chunks={legacy_flat_count}")
            print(f"  qdrant_vectors={len(vector_ids)} checked_vectors={checked}")
            print(f"  missing_vectors={len(missing)} orphan_vectors={len(orphan)} zero_vectors={len(zero_ids)}")
            print(f"  child_without_parent={len(child_without_parent)}")
            if orphan_zero_ids:
                print(f"  orphan_zero_vectors={', '.join(orphan_zero_ids)}")

            if missing or orphan or zero_ids or child_without_parent or (legacy_flat_count and not args.allow_flat_chunks):
                ok = False

        if not ok:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
