#!/usr/bin/env python3
"""Repair historical child chunks that were stored without parent links.

Run inside the API container:
    python /app/scripts/repair_parent_child_links.py --reembed
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import select

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Chunk, Course, Document
from app.services.chunking import CURRENT_EMBEDDING_TEXT_VERSION, contextual_embedding_text
from app.services.embeddings import EmbeddingProvider, validate_embedding_vectors
from app.services.ingestion import chunk_context_summary
from app.services.vector_store import VectorStore


def metadata(chunk: Chunk) -> dict:
    return dict(chunk.metadata_json or {})


def section_key(chunk: Chunk) -> tuple[str, str]:
    meta = metadata(chunk)
    return (chunk.document_version_id, str(meta.get("section_index", "")))


def child_sort_key(chunk: Chunk) -> tuple[str, int, int, str]:
    meta = metadata(chunk)
    try:
        section_index = int(meta.get("section_index") or 0)
    except (TypeError, ValueError):
        section_index = 0
    try:
        chunk_index = int(meta.get("chunk_index") or 0)
    except (TypeError, ValueError):
        chunk_index = 0
    return (chunk.document_id, section_index, chunk_index, chunk.id)


def build_parent_index(chunks: list[Chunk]) -> dict[tuple[str, str], Chunk]:
    parents: dict[tuple[str, str], Chunk] = {}
    for chunk in chunks:
        meta = metadata(chunk)
        if meta.get("is_parent") is not True:
            continue
        key = section_key(chunk)
        if key[1] == "":
            continue
        current = parents.get(key)
        if current is None or child_sort_key(chunk) < child_sort_key(current):
            parents[key] = chunk
    return parents


def payload_for_chunk(chunk: Chunk, document: Document | None, course_id: str) -> dict:
    meta = metadata(chunk)
    return {
        "chunk_id": chunk.id,
        "course_id": course_id,
        "document_id": chunk.document_id,
        "document_title": document.title if document else "Unknown",
        "source_path": document.source_path if document else "",
        "chapter": chunk.chapter,
        "section": chunk.section,
        "page_number": chunk.page_number,
        "snippet": chunk.snippet,
        "source_type": chunk.source_type,
        "version": 1,
        "tags": document.tags if document else [],
        "difficulty": document.difficulty if document else None,
        "content": chunk.content,
        "content_kind": meta.get("content_kind"),
        "is_parent": meta.get("is_parent", False),
        "parent_chunk_id": str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
        "embedding_text_version": CURRENT_EMBEDDING_TEXT_VERSION,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="Repair missing parent_chunk_id links for historical child chunks.")
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reembed", action="store_true", help="Re-embed repaired child chunks with parent/neighbor context.")
    parser.add_argument("--batch-size", type=int, default=10)
    args = parser.parse_args()

    settings = get_settings()
    if args.reembed:
        print(f"Embedding model: {settings.embedding_model}")
        print(f"Embedding dimensions: {settings.embedding_dimensions}")
        print(f"Embedding provider external: {bool(settings.openai_api_key)}")

    with SessionLocal() as db:
        course_query = select(Course)
        if args.course_id:
            course_query = course_query.where(Course.id == args.course_id)
        if args.course_name:
            course_query = course_query.where(Course.name == args.course_name)
        courses = db.scalars(course_query.order_by(Course.name.asc())).all()
        if not courses:
            raise SystemExit("No matching courses found.")

        repaired_total = 0
        reembedded_total = 0
        missing_parent_total = 0

        for course in courses:
            chunks = db.scalars(select(Chunk).where(Chunk.course_id == course.id, Chunk.is_active.is_(True))).all()
            parent_index = build_parent_index(chunks)
            children = [
                chunk
                for chunk in chunks
                if metadata(chunk).get("is_parent") is False and not chunk.parent_chunk_id
            ]
            children.sort(key=child_sort_key)
            repaired: list[Chunk] = []
            missing_parent: list[Chunk] = []
            for child in children:
                parent = parent_index.get(section_key(child))
                if parent is None:
                    missing_parent.append(child)
                    continue
                child.parent_chunk_id = parent.id
                child_meta = metadata(child)
                child_meta["parent_chunk_id"] = parent.id
                child.metadata_json = child_meta
                repaired.append(child)

            print(f"\nCourse: {course.name}")
            print(f"  children_without_parent={len(children)}")
            print(f"  repaired={len(repaired)} missing_matching_parent={len(missing_parent)}")
            repaired_total += len(repaired)
            missing_parent_total += len(missing_parent)

            if args.dry_run or not repaired:
                db.rollback()
                continue

            db.commit()

            vector_store = VectorStore(course_name=course.name)
            documents = {document.id: document for document in db.scalars(select(Document).where(Document.course_id == course.id)).all()}
            repaired_ids = [chunk.id for chunk in repaired]
            existing_points: dict[str, dict] = {}
            for start in range(0, len(repaired_ids), 100):
                for point in vector_store.get_points(repaired_ids[start : start + 100]):
                    existing_points[str(point["id"])] = point

            if not args.reembed:
                points = []
                for chunk in repaired:
                    current = existing_points.get(chunk.id)
                    if current is None:
                        continue
                    points.append(
                        {
                            "id": chunk.id,
                            "vector": current["vector"],
                            "payload": payload_for_chunk(chunk, documents.get(chunk.document_id), course.id),
                        }
                    )
                vector_store.upsert(points)
                print(f"  qdrant_payload_updated={len(points)}")
                continue

            siblings_by_section: dict[tuple[str, str], list[Chunk]] = defaultdict(list)
            for chunk in chunks:
                if metadata(chunk).get("is_parent") is False:
                    siblings_by_section[section_key(chunk)].append(chunk)
            for siblings in siblings_by_section.values():
                siblings.sort(key=child_sort_key)

            embedder = EmbeddingProvider()
            for start in range(0, len(repaired), args.batch_size):
                batch = repaired[start : start + args.batch_size]
                texts = []
                for chunk in batch:
                    meta = metadata(chunk)
                    parent = db.get(Chunk, chunk.parent_chunk_id) if chunk.parent_chunk_id else None
                    siblings = siblings_by_section.get(section_key(chunk), [])
                    index = next((idx for idx, candidate in enumerate(siblings) if candidate.id == chunk.id), -1)
                    prev_chunk = siblings[index - 1] if index > 0 else None
                    next_chunk = siblings[index + 1] if index >= 0 and index + 1 < len(siblings) else None
                    document = documents.get(chunk.document_id)
                    texts.append(
                        contextual_embedding_text(
                            document_title=document.title if document else "Unknown",
                            chapter=chunk.chapter,
                            section=chunk.section,
                            source_type=chunk.source_type,
                            content_kind=meta.get("content_kind"),
                            content=chunk.content,
                            parent_summary=chunk_context_summary(parent),
                            prev_summary=chunk_context_summary(prev_chunk),
                            next_summary=chunk_context_summary(next_chunk),
                            summary=chunk.summary,
                            keywords=chunk.keywords or None,
                            has_table=bool(meta.get("has_table")),
                            has_formula=bool(meta.get("has_formula")),
                        )
                    )
                result = await embedder.embed_texts_with_meta(texts, text_type="document")
                validate_embedding_vectors(
                    result.vectors,
                    expected_count=len(texts),
                    expected_dimensions=settings.embedding_dimensions,
                )
                points = [
                    {
                        "id": chunk.id,
                        "vector": vector,
                        "payload": payload_for_chunk(chunk, documents.get(chunk.document_id), course.id),
                    }
                    for chunk, vector in zip(batch, result.vectors)
                ]
                vector_store.upsert(points)
                reembedded_total += len(points)
                print(f"  reembedded={reembedded_total} provider={result.provider}")

        print("\nSummary")
        print(f"  repaired_total={repaired_total}")
        print(f"  missing_parent_total={missing_parent_total}")
        print(f"  reembedded_total={reembedded_total}")
        if missing_parent_total:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
