#!/usr/bin/env python3
"""Analyze chunk quality across courses from the configured database.

Run inside the API container:
    python /app/scripts/analyze_chunk_quality.py --course-name "Course Name"
"""
import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Chunk, Course, Document


def analyze(course_name: str | None, include_inactive: bool):
    db = SessionLocal()
    query = select(Chunk, Document, Course).join(Document, Chunk.document_id == Document.id).join(Course, Chunk.course_id == Course.id)
    if course_name:
        query = query.where(Course.name == course_name)
    if not include_inactive:
        query = query.where(Chunk.is_active.is_(True), Document.is_active.is_(True))
    rows = db.execute(query).all()

    print(f"=== Total chunks: {len(rows)} ===\n")

    # 1. content_kind distribution
    kind_counts = Counter(((chunk.metadata_json or {}).get("content_kind", "unknown")) for chunk, _doc, _course in rows)
    print("=== content_kind distribution ===")
    for kind, count in kind_counts.most_common():
        print(f"  {kind}: {count}")

    # 2. has_table / has_formula distribution
    print("\n=== has_table distribution ===")
    table_counts = Counter((chunk.metadata_json or {}).get("has_table") for chunk, _doc, _course in rows)
    for val, count in table_counts.most_common():
        print(f"  {val}: {count}")

    print("\n=== has_formula distribution ===")
    formula_counts = Counter((chunk.metadata_json or {}).get("has_formula") for chunk, _doc, _course in rows)
    for val, count in formula_counts.most_common():
        print(f"  {val}: {count}")

    # 3. Per-course stats
    print("\n=== Per-course stats ===")
    ALL_KINDS = ["pdf_page", "text", "table", "formula", "mixed", "markdown", "code", "html", "other", "unknown"]
    course_stats = defaultdict(lambda: {k: 0 for k in ALL_KINDS + [
        "total", "has_table", "has_formula", "high_digit_ratio",
        "is_parent", "is_child",
    ]})
    for chunk, document, course_obj in rows:
        course = course_obj.name
        stats = course_stats[course]
        stats["total"] += 1
        meta = chunk.metadata_json or {}
        kind = meta.get("content_kind") or "unknown"
        if kind in stats:
            stats[kind] += 1
        else:
            stats["other"] += 1
        if meta.get("has_table"):
            stats["has_table"] += 1
        if meta.get("has_formula"):
            stats["has_formula"] += 1
        if meta.get("is_parent"):
            stats["is_parent"] += 1
        if chunk.parent_chunk_id:
            stats["is_child"] += 1

        text = chunk.content or ""
        digits_dots = sum(1 for c in text if c.isdigit() or c == ".")
        if len(text) > 0 and digits_dots / len(text) > 0.6:
            stats["high_digit_ratio"] += 1

    for course, stats in sorted(course_stats.items()):
        print(f"\n  {course}:")
        print(f"    Total chunks: {stats['total']}")
        print(f"    content_kind: pdf_page={stats['pdf_page']}, text={stats['text']}, "
              f"table={stats['table']}, formula={stats['formula']}, "
              f"markdown={stats['markdown']}, code={stats['code']}, html={stats['html']}, "
              f"other={stats['other']}, unknown={stats['unknown']}")
        print(f"    has_table={stats['has_table']}, has_formula={stats['has_formula']}")
        print(f"    is_parent={stats['is_parent']}, is_child={stats['is_child']}")
        print(f"    High digit ratio (>60%): {stats['high_digit_ratio']}")

    # 4. High digit ratio chunks (potential problems)
    print("\n=== High digit ratio (>60%) chunks ===")
    high_digit_chunks = []
    for chunk, document, course_obj in rows:
        text = chunk.content or ""
        if len(text) > 50:
            digits_dots = sum(1 for c in text if c.isdigit() or c == ".")
            ratio = digits_dots / len(text)
            if ratio > 0.6:
                high_digit_chunks.append((ratio, chunk, document, course_obj))

    high_digit_chunks.sort(key=lambda x: x[0], reverse=True)
    for ratio, chunk, document, course_obj in high_digit_chunks:
        meta = chunk.metadata_json or {}
        text_preview = (chunk.content or "")[:80].replace("\n", " ")
        kind = meta.get("content_kind", "unknown")
        has_table = meta.get("has_table")
        has_formula = meta.get("has_formula")
        print(f"  [{course_obj.name}] {document.title} | "
              f"kind={kind} has_table={has_table} has_formula={has_formula} | "
              f"digit_ratio={ratio:.1%} len={len(chunk.content or '')} | "
              f"{text_preview}...")

    # 5. File list
    files = db.execute(select(Document, Course).join(Course, Document.course_id == Course.id).order_by(Course.name, Document.title)).all()
    print("\n=== All source files ===")
    for document, course_obj in files:
        print(f"  [{course_obj.name}] {document.title} ({document.source_type})")

    # 6. Check embedding version distribution
    print("\n=== embedding_version distribution ===")
    version_counts = Counter(chunk.embedding_text_version or "unknown" for chunk, _doc, _course in rows)
    for ver, count in version_counts.most_common():
        print(f"  {ver}: {count}")

    # 7. Check embedding_quality_score distribution
    print("\n=== embedding_quality_score stats ===")
    scores = []
    for chunk, _doc, _course in rows:
        score = (chunk.metadata_json or {}).get("embedding_quality_score")
        if score is not None:
            scores.append(score)
    if scores:
        import statistics
        print(f"  Count: {len(scores)}")
        print(f"  Min: {min(scores):.3f}")
        print(f"  Max: {max(scores):.3f}")
        print(f"  Mean: {statistics.mean(scores):.3f}")
        print(f"  Median: {statistics.median(scores):.3f}")
    else:
        print("  No scores found")

    # 8. Check for duplicate chunks (same content)
    print("\n=== Duplicate content analysis ===")
    content_counts = Counter(chunk.content or "" for chunk, _doc, _course in rows)
    duplicates = [(content, count) for content, count in content_counts.most_common() if count > 1 and len(content) > 20]
    print(f"  Unique contents: {len(content_counts)}")
    print(f"  Contents with duplicates: {len(duplicates)}")
    print(f"  Total duplicate instances: {sum(c for _, c in duplicates)}")
    for content, count in duplicates[:10]:
        preview = content[:60].replace("\n", " ")
        print(f"    x{count}: {preview}...")

    # 9. Chunk size distribution
    print("\n=== Chunk size (characters) distribution ===")
    sizes = [len(chunk.content or "") for chunk, _doc, _course in rows]
    import statistics
    if sizes:
        print(f"  Count: {len(sizes)}")
        print(f"  Min: {min(sizes)}")
        print(f"  Max: {max(sizes)}")
        print(f"  Mean: {statistics.mean(sizes):.0f}")
        print(f"  Median: {statistics.median(sizes):.0f}")
    else:
        print("  No chunks found")

    # 10. Chunk size by content_kind
    print("\n=== Chunk size by content_kind ===")
    kind_sizes = defaultdict(list)
    for chunk, _doc, _course in rows:
        kind = (chunk.metadata_json or {}).get("content_kind", "unknown")
        kind_sizes[kind].append(len(chunk.content or ""))
    for kind, sizes in sorted(kind_sizes.items()):
        print(f"  {kind}: min={min(sizes)}, max={max(sizes)}, mean={statistics.mean(sizes):.0f}, median={statistics.median(sizes):.0f}")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze chunk quality from the configured database.")
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--include-inactive", action="store_true")
    args = parser.parse_args()
    analyze(args.course_name, args.include_inactive)
