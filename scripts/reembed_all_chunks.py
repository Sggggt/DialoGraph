"""Repair zero vectors with the container's configured embedding provider.

Run inside the API container:
    python /app/scripts/reembed_all_chunks.py --course-name "Course Name" --dry-run

This script:
1. Reads all active chunks from the DB.
2. Checks their vectors in Qdrant.
3. Re-embeds zero-vector chunks with contextual parent/neighbor input.
4. Upserts corrected vectors back to Qdrant.

It refuses to run when model or database fallback is enabled.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

# Ensure the API app is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Chunk, Course, Document
from app.services.chunking import CURRENT_EMBEDDING_TEXT_VERSION, contextual_embedding_text
from app.services.embeddings import EmbeddingProvider, validate_embedding_vectors
from app.services.ingestion import chunk_context_summary
from app.services.vector_store import VectorStore
from sqlalchemy import select


def vector_is_zero(vec: list[float]) -> bool:
    return all(abs(v) < 1e-12 for v in vec)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed zero-vector chunks")
    parser.add_argument("--dry-run", action="store_true", help="Only report, don't write")
    parser.add_argument("--batch-size", type=int, default=10, help="Embedding batch size")
    parser.add_argument("--course-id", type=str, default=None, help="Limit to a specific course id")
    parser.add_argument("--course-name", type=str, default=None, help="Limit to a specific course name")
    args = parser.parse_args()

    settings = get_settings()
    if settings.enable_model_fallback or settings.enable_database_fallback:
        raise SystemExit("Refusing to re-embed while fallback is enabled.")
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for real no-fallback re-embedding.")
    print(f"Embedding model: {settings.embedding_model}")
    print(f"Embedding dimensions: {settings.embedding_dimensions}")
    print(f"Embedding base URL: {settings.embedding_base_url}")
    print(f"Has API key: {bool(settings.openai_api_key)}")
    print()

    with SessionLocal() as db:
        query = select(Chunk).where(Chunk.is_active.is_(True))
        if args.course_id:
            query = query.where(Chunk.course_id == args.course_id)
        if args.course_name:
            course_id = db.scalars(select(Course.id).where(Course.name == args.course_name)).first()
            if not course_id:
                raise SystemExit(f"No course found named {args.course_name!r}.")
            query = query.where(Chunk.course_id == course_id)
        chunks = db.scalars(query).all()
        print(f"Total active chunks: {len(chunks)}")

        # Group chunks by course for VectorStore lookup
        chunks_by_course: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            chunks_by_course.setdefault(chunk.course_id, []).append(chunk)

        total_zero = 0
        total_fixed = 0

        for course_id, course_chunks in chunks_by_course.items():
            course = db.get(Course, course_id)
            if course is None:
                print(f"  [SKIP] Course {course_id} not found")
                continue

            print(f"\n--- Course: {course.name} ({len(course_chunks)} chunks) ---")
            vs = VectorStore(course_name=course.name)

            # Check which vectors are zero in Qdrant
            chunk_ids = [c.id for c in course_chunks]
            zero_chunks: list[Chunk] = []

            # Check in batches of 100
            for i in range(0, len(chunk_ids), 100):
                batch_ids = chunk_ids[i : i + 100]
                points = vs.get_points(batch_ids)
                points_by_id = {p["id"]: p for p in points}
                for cid in batch_ids:
                    pt = points_by_id.get(cid)
                    if pt is None or vector_is_zero(pt["vector"]):
                        zero_chunks.append(
                            next(c for c in course_chunks if c.id == cid)
                        )

            print(f"  Zero-vector chunks: {len(zero_chunks)} / {len(course_chunks)}")
            total_zero += len(zero_chunks)

            if args.dry_run or not zero_chunks:
                continue

            siblings_by_section: dict[tuple[str, str], list[Chunk]] = {}
            parents_by_section: dict[tuple[str, str], Chunk] = {}
            for chunk in course_chunks:
                meta = chunk.metadata_json or {}
                key = (chunk.document_version_id, str(meta.get("section_index", "")))
                if meta.get("is_parent") is True:
                    parents_by_section[key] = chunk
                elif meta.get("is_parent") is False:
                    siblings_by_section.setdefault(key, []).append(chunk)
            for siblings in siblings_by_section.values():
                siblings.sort(
                    key=lambda item: (
                        item.document_id,
                        int((item.metadata_json or {}).get("section_index") or 0),
                        int((item.metadata_json or {}).get("chunk_index") or 0),
                        item.id,
                    )
                )

            # Re-embed in batches
            embedder = EmbeddingProvider()
            for i in range(0, len(zero_chunks), args.batch_size):
                batch = zero_chunks[i : i + args.batch_size]
                docs = {
                    c.document_id: db.get(Document, c.document_id) for c in batch
                }
                texts = []
                for c in batch:
                    meta = c.metadata_json or {}
                    section_key = (c.document_version_id, str(meta.get("section_index", "")))
                    siblings = siblings_by_section.get(section_key, [])
                    sibling_index = next((idx for idx, item in enumerate(siblings) if item.id == c.id), -1)
                    prev_chunk = siblings[sibling_index - 1] if sibling_index > 0 else None
                    next_chunk = siblings[sibling_index + 1] if sibling_index >= 0 and sibling_index + 1 < len(siblings) else None
                    parent_chunk = db.get(Chunk, c.parent_chunk_id) if c.parent_chunk_id else parents_by_section.get(section_key)
                    texts.append(
                        contextual_embedding_text(
                            document_title=docs[c.document_id].title
                            if docs.get(c.document_id)
                            else "Unknown",
                            chapter=c.chapter,
                            section=c.section,
                            source_type=c.source_type,
                            content_kind=meta.get("content_kind"),
                            content=c.content,
                            parent_summary=chunk_context_summary(parent_chunk),
                            prev_summary=chunk_context_summary(prev_chunk),
                            next_summary=chunk_context_summary(next_chunk),
                            summary=c.summary,
                            keywords=c.keywords or None,
                            has_table=meta.get("has_table", False),
                            has_formula=meta.get("has_formula", False),
                        )
                    )

                result = await embedder.embed_texts_with_meta(
                    texts, text_type="document"
                )
                try:
                    validate_embedding_vectors(
                        result.vectors,
                        expected_count=len(texts),
                        expected_dimensions=settings.embedding_dimensions,
                    )
                except RuntimeError as e:
                    print(f"  [ERROR] Validation failed at batch {i}: {e}")
                    continue

                # Build Qdrant points
                points = []
                for chunk, vec in zip(batch, result.vectors):
                    doc = docs.get(chunk.document_id)
                    points.append(
                        {
                            "id": chunk.id,
                            "vector": vec,
                            "payload": {
                                "chunk_id": chunk.id,
                                "course_id": course_id,
                                "document_id": chunk.document_id,
                                "document_title": doc.title if doc else "Unknown",
                                "source_path": doc.source_path if doc else "",
                                "chapter": chunk.chapter,
                                "section": chunk.section,
                                "page_number": chunk.page_number,
                                "snippet": chunk.snippet,
                                "source_type": chunk.source_type,
                                "version": 1,
                                "tags": doc.tags if doc else [],
                                "difficulty": doc.difficulty if doc else None,
                                "content": chunk.content,
                                "content_kind": (chunk.metadata_json or {}).get(
                                    "content_kind"
                                ),
                                "is_parent": (chunk.metadata_json or {}).get("is_parent", False),
                                "parent_chunk_id": str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
                                "embedding_text_version": CURRENT_EMBEDDING_TEXT_VERSION,
                            },
                        }
                    )
                vs.upsert(points)
                total_fixed += len(batch)
                pct = (i + len(batch)) / len(zero_chunks) * 100
                print(
                    f"  [{i + len(batch)}/{len(zero_chunks)}] ({pct:.0f}%) "
                    f"re-embedded via {result.provider}"
                )

        print(f"\n=== Summary ===")
        print(f"Total zero-vector chunks found: {total_zero}")
        if args.dry_run:
            print("Dry run: no changes made.")
        else:
            print(f"Successfully re-embedded: {total_fixed}")


if __name__ == "__main__":
    asyncio.run(main())
