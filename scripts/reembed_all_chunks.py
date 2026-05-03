"""Re-embed all active chunks that have zero vectors in Qdrant.

Usage (run from apps/api directory):
    python ../../scripts/reembed_all_chunks.py [--dry-run] [--batch-size 10]

This script:
1. Reads all active chunks from the DB.
2. Checks their vectors in Qdrant.
3. Re-embeds any with zero vectors using the configured embedding model.
4. Upserts the corrected vectors back to Qdrant.
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
from app.models import Chunk, Document
from app.services.chunking import EMBEDDING_TEXT_VERSION, embedding_text
from app.services.embeddings import EmbeddingProvider, validate_embedding_vectors
from app.services.vector_store import VectorStore
from sqlalchemy import select


def vector_is_zero(vec: list[float]) -> bool:
    return all(abs(v) < 1e-12 for v in vec)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Re-embed zero-vector chunks")
    parser.add_argument("--dry-run", action="store_true", help="Only report, don't write")
    parser.add_argument("--batch-size", type=int, default=10, help="Embedding batch size")
    parser.add_argument("--course-id", type=str, default=None, help="Limit to specific course")
    args = parser.parse_args()

    settings = get_settings()
    print(f"Embedding model: {settings.embedding_model}")
    print(f"Embedding dimensions: {settings.embedding_dimensions}")
    print(f"Base URL: {settings.openai_base_url}")
    print(f"Has API key: {bool(settings.openai_api_key)}")
    print()

    with SessionLocal() as db:
        query = select(Chunk).where(Chunk.is_active.is_(True))
        if args.course_id:
            query = query.where(Chunk.course_id == args.course_id)
        chunks = db.scalars(query).all()
        print(f"Total active chunks: {len(chunks)}")

        # Group chunks by course for VectorStore lookup
        chunks_by_course: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            chunks_by_course.setdefault(chunk.course_id, []).append(chunk)

        total_zero = 0
        total_fixed = 0

        for course_id, course_chunks in chunks_by_course.items():
            course = db.get(
                __import__("app.models", fromlist=["Course"]).Course, course_id
            )
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

            # Re-embed in batches
            embedder = EmbeddingProvider()
            for i in range(0, len(zero_chunks), args.batch_size):
                batch = zero_chunks[i : i + args.batch_size]
                docs = {
                    c.document_id: db.get(Document, c.document_id) for c in batch
                }
                texts = [
                    embedding_text(
                        document_title=docs[c.document_id].title
                        if docs.get(c.document_id)
                        else "Unknown",
                        chapter=c.chapter,
                        section=c.section,
                        source_type=c.source_type,
                        content_kind=(c.metadata_json or {}).get("content_kind"),
                        content=c.content,
                    )
                    for c in batch
                ]

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
                                "embedding_text_version": EMBEDDING_TEXT_VERSION,
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
            print("Dry run — no changes made.")
        else:
            print(f"Successfully re-embedded: {total_fixed}")


if __name__ == "__main__":
    asyncio.run(main())
