#!/usr/bin/env python3
"""Re-embed active chunks with the current contextual embedding input.

Run inside the API container:
    python /app/scripts/reembed_with_enhancement.py --course-name "Course Name" --dry-run

The script updates Qdrant vectors and the DB embedding_text_version. It refuses
to run when model or database fallback is enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import Chunk, Course, Document
from app.services.chunking import CURRENT_EMBEDDING_TEXT_VERSION, contextual_embedding_text
from app.services.embeddings import EmbeddingProvider, validate_embedding_vectors
from app.services.vector_store import VectorStore


def chunk_context_summary(chunk: Chunk | None, max_chars: int = 150) -> str | None:
    if chunk is None:
        return None
    for value in (chunk.summary, chunk.snippet, chunk.content):
        text = (value or "").strip()
        if text:
            return text[:max_chars]
    return None


async def reembed_chunks(course_name: str | None, course_id: str | None, batch_size: int, dry_run: bool) -> None:
    settings = get_settings()
    if settings.enable_model_fallback or settings.enable_database_fallback:
        raise SystemExit("Refusing to re-embed while fallback is enabled.")
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for real no-fallback re-embedding.")

    db = SessionLocal()
    try:
        query = db.query(Chunk).filter(Chunk.is_active.is_(True))
        if course_id:
            query = query.filter(Chunk.course_id == course_id)
        if course_name:
            matched_course_id = db.query(Course.id).filter(Course.name == course_name).scalar()
            if not matched_course_id:
                raise SystemExit(f"No course found named {course_name!r}.")
            query = query.filter(Chunk.course_id == matched_course_id)
        chunks = query.all()
        if not chunks:
            print("No active chunks found.")
            return

        print(f"Found {len(chunks)} active chunks to re-embed.")
        if dry_run:
            print("Dry run: vectors and DB rows will not be modified.")

        course_chunks: dict[str, list[Chunk]] = {}
        for chunk in chunks:
            course_chunks.setdefault(chunk.course_id, []).append(chunk)

        embedder = None if dry_run else EmbeddingProvider()

        for course_id, course_chunk_list in course_chunks.items():
            course = db.get(Course, course_id)
            if not course:
                continue

            vector_store = VectorStore(course_name=course.name)
            total = len(course_chunk_list)
            documents = {doc.id: doc for doc in db.query(Document).filter(Document.course_id == course_id).all()}
            chunks_by_id = {chunk.id: chunk for chunk in course_chunk_list}
            children_by_parent: dict[str, list[Chunk]] = {}
            for chunk in course_chunk_list:
                if chunk.parent_chunk_id:
                    children_by_parent.setdefault(chunk.parent_chunk_id, []).append(chunk)
            for children in children_by_parent.values():
                children.sort(key=lambda c: ((c.metadata_json or {}).get("section_index", 0), (c.metadata_json or {}).get("chunk_index", 0), c.created_at))

            for i in range(0, total, batch_size):
                batch = course_chunk_list[i : i + batch_size]
                embedding_inputs = []

                for chunk in batch:
                    document = documents.get(chunk.document_id)
                    parent_chunk = chunks_by_id.get(chunk.parent_chunk_id or "")
                    metadata = chunk.metadata_json or {}
                    prev_summary = None
                    next_summary = None
                    if chunk.parent_chunk_id:
                        siblings = children_by_parent.get(chunk.parent_chunk_id, [])
                        index = next((idx for idx, item in enumerate(siblings) if item.id == chunk.id), -1)
                        prev_summary = chunk_context_summary(siblings[index - 1]) if index > 0 else None
                        next_summary = chunk_context_summary(siblings[index + 1]) if index >= 0 and index + 1 < len(siblings) else None

                    embedding_inputs.append(
                        contextual_embedding_text(
                            document_title=document.title if document else "",
                            chapter=chunk.chapter,
                            section=chunk.section,
                            source_type=chunk.source_type,
                            content_kind=metadata.get("content_kind"),
                            content=chunk.content,
                            parent_summary=chunk_context_summary(parent_chunk, max_chars=200),
                            prev_summary=prev_summary,
                            next_summary=next_summary,
                            summary=chunk.summary,
                            keywords=chunk.keywords or None,
                            has_table=metadata.get("has_table", False),
                            has_formula=metadata.get("has_formula", False),
                        )
                    )

                if dry_run:
                    print(f"  [{course.name}] Would re-embed batch {i // batch_size + 1}/{(total - 1) // batch_size + 1} ({len(batch)} chunks)")
                    continue

                assert embedder is not None
                result = await embedder.embed_texts_with_meta(embedding_inputs, text_type="document")
                vectors = result.vectors
                validate_embedding_vectors(vectors, expected_count=len(batch), expected_dimensions=embedder.settings.embedding_dimensions)

                vector_points = []
                for chunk, vector in zip(batch, vectors):
                    document = documents.get(chunk.document_id)
                    metadata = dict(chunk.metadata_json or {})
                    chunk.embedding_text_version = CURRENT_EMBEDDING_TEXT_VERSION
                    metadata["embedding_text_version"] = CURRENT_EMBEDDING_TEXT_VERSION
                    chunk.metadata_json = metadata
                    vector_points.append(
                        {
                            "id": chunk.id,
                            "vector": vector,
                            "payload": {
                                "chunk_id": chunk.id,
                                "course_id": course_id,
                                "document_id": chunk.document_id,
                                "document_title": document.title if document else "",
                                "chapter": chunk.chapter,
                                "section": chunk.section,
                                "page_number": chunk.page_number,
                                "snippet": chunk.snippet,
                                "source_type": chunk.source_type,
                                "content": chunk.content,
                                "content_kind": metadata.get("content_kind"),
                                "is_parent": metadata.get("is_parent", False),
                                "parent_chunk_id": str(chunk.parent_chunk_id) if chunk.parent_chunk_id else None,
                                "embedding_text_version": CURRENT_EMBEDDING_TEXT_VERSION,
                            },
                        }
                    )

                vector_store.upsert(vector_points)
                db.commit()
                print(f"  [{course.name}] Re-embedded batch {i // batch_size + 1}/{(total - 1) // batch_size + 1} ({len(batch)} chunks)")

        print("Re-embedding complete.")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-embed active chunks with contextual embedding input.")
    parser.add_argument("--course-name", default=None)
    parser.add_argument("--course-id", default=None)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(reembed_chunks(args.course_name, args.course_id, args.batch_size, args.dry_run))
