from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, Concept, Course, Document, DocumentVersion, IngestionBatch, IngestionJob
from app.services.chunking import chunk_sections
from app.services.concept_graph import get_concept_cards, get_graph_payload, upsert_concepts_from_chunk
from app.services.embeddings import EmbeddingProvider, is_degraded_mode
from app.services.parsers import derive_chapter, parse_document, sections_to_json
from app.services.storage import compute_checksum, copy_source_file
from app.services.vector_store import VectorStore


ALLOWED_SUFFIXES = {".pdf", ".ipynb", ".md", ".markdown", ".txt", ".docx", ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".bmp"}
EXCLUDED_PARTS = {"output", "tmp", "scripts", ".ipynb_checkpoints", "__pycache__"}
IGNORED_NAMES = {".ds_store"}
TERMINAL_STATES = {"completed", "failed", "partial_failed", "skipped"}


def get_course_paths(course_name: str) -> dict[str, Path]:
    settings = get_settings()
    return settings.course_paths_for_name(course_name)


def ensure_course_directories(course_name: str) -> dict[str, Path]:
    paths = get_course_paths(course_name)
    for key in ("course_root", "source_root", "storage_root", "ingestion_root"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def copy_tree_contents(source_root: Path, target_root: Path) -> None:
    if not source_root.exists():
        return
    target_root.mkdir(parents=True, exist_ok=True)
    for item in source_root.iterdir():
        destination = target_root / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def migrate_legacy_course_layout(db: Session, course: Course, paths: dict[str, Path]) -> None:
    legacy_source_root = Path(course.source_root)
    canonical_source_root = paths["source_root"]
    storage_root = paths["storage_root"]
    ingestion_root = paths["ingestion_root"]
    settings = get_settings()

    configured_source_root = settings.course_source_root_path if course.name == settings.course_name and settings.course_source_root else None
    if configured_source_root and configured_source_root != canonical_source_root and configured_source_root.exists():
        legacy_source_root = configured_source_root

    legacy_shared_storage = Path(settings.storage_root) if settings.storage_root else None
    legacy_shared_ingestion = Path(settings.ingestion_root) if settings.ingestion_root else None

    if legacy_source_root != canonical_source_root and legacy_source_root.exists():
        copy_tree_contents(legacy_source_root, canonical_source_root)

    if legacy_shared_storage and legacy_shared_storage.exists() and legacy_shared_storage != storage_root:
        copy_tree_contents(legacy_shared_storage, storage_root)

    if legacy_shared_ingestion and legacy_shared_ingestion.exists() and legacy_shared_ingestion != ingestion_root:
        copy_tree_contents(legacy_shared_ingestion, ingestion_root)

    old_source_prefix = str(legacy_source_root)
    new_source_prefix = str(canonical_source_root)
    old_storage_prefix = str(legacy_shared_storage) if legacy_shared_storage else None
    new_storage_prefix = str(storage_root)
    old_ingestion_prefix = str(legacy_shared_ingestion) if legacy_shared_ingestion else None
    new_ingestion_prefix = str(ingestion_root)

    for document in db.scalars(select(Document).where(Document.course_id == course.id)).all():
        if document.source_path.startswith(old_source_prefix):
            document.source_path = document.source_path.replace(old_source_prefix, new_source_prefix, 1)

    for version in db.scalars(
        select(DocumentVersion).join(Document, Document.id == DocumentVersion.document_id).where(Document.course_id == course.id)
    ).all():
        if old_storage_prefix and version.storage_path.startswith(old_storage_prefix):
            version.storage_path = version.storage_path.replace(old_storage_prefix, new_storage_prefix, 1)
        if version.extracted_path and old_ingestion_prefix and version.extracted_path.startswith(old_ingestion_prefix):
            version.extracted_path = version.extracted_path.replace(old_ingestion_prefix, new_ingestion_prefix, 1)

    for batch in db.scalars(select(IngestionBatch).where(IngestionBatch.course_id == course.id)).all():
        if batch.source_root.startswith(old_source_prefix):
            batch.source_root = batch.source_root.replace(old_source_prefix, new_source_prefix, 1)

    for job in db.scalars(select(IngestionJob).where(IngestionJob.course_id == course.id)).all():
        if job.source_path and job.source_path.startswith(old_source_prefix):
            job.source_path = job.source_path.replace(old_source_prefix, new_source_prefix, 1)

    course.source_root = new_source_prefix
    db.commit()
    db.refresh(course)


def summarize_course(db: Session, course: Course) -> dict:
    document_count = db.query(Document).filter(Document.course_id == course.id, Document.is_active.is_(True)).count()
    concept_count = db.query(Concept).filter(Concept.course_id == course.id).count()
    return {
        "id": course.id,
        "name": course.name,
        "description": course.description,
        "source_root": course.source_root,
        "document_count": document_count,
        "concept_count": concept_count,
        "degraded_mode": is_degraded_mode(),
    }


def create_course_space(db: Session, name: str, description: str | None = None) -> Course:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Course name cannot be empty")
    paths = ensure_course_directories(normalized_name)
    course = db.scalar(select(Course).where(Course.name == normalized_name))
    if course is None:
        course = Course(name=normalized_name, description=description, source_root=str(paths["source_root"]))
        db.add(course)
        db.commit()
        db.refresh(course)
        return course
    if description is not None:
        course.description = description
    migrate_legacy_course_layout(db, course, paths)
    return course


def ensure_current_course(db: Session) -> Course:
    settings = get_settings()
    return create_course_space(db, settings.course_name)


def resolve_course(db: Session, course_id: str | None = None) -> Course:
    if course_id is None:
        return ensure_current_course(db)
    course = db.get(Course, course_id)
    if course is None:
        raise LookupError(f"Course not found: {course_id}")
    paths = ensure_course_directories(course.name)
    if course.source_root != str(paths["source_root"]):
        migrate_legacy_course_layout(db, course, paths)
    return course


def list_course_summaries(db: Session) -> list[dict]:
    courses = db.scalars(select(Course).order_by(Course.created_at.asc())).all()
    if not courses:
        courses = [ensure_current_course(db)]
    return [summarize_course(db, course) for course in courses]


def should_include_source(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name.lower() in IGNORED_NAMES or path.name.startswith("~$"):
        return False
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        return False
    return not any(part.lower() in EXCLUDED_PARTS for part in path.parts)


def collect_source_documents(root: Path) -> list[Path]:
    return sorted((path for path in root.rglob("*") if should_include_source(path)), key=lambda item: str(item).lower())


def create_job(
    db: Session,
    course_id: str,
    document_id: str | None,
    trigger_source: str,
    batch_id: str | None = None,
    source_path: str | None = None,
) -> IngestionJob:
    job = IngestionJob(
        course_id=course_id,
        document_id=document_id,
        batch_id=batch_id,
        source_path=source_path,
        trigger_source=trigger_source,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def set_job_state(db: Session, job: IngestionJob, state: str, *, error: str | None = None, batch_id: str | None = None) -> None:
    job.status = state
    if error is not None:
        job.error_message = error
    batch = db.get(IngestionBatch, batch_id) if batch_id else None
    if batch and state not in {"completed", "failed", "partial_failed", "skipped"}:
        batch.status = state
        batch.started_at = batch.started_at or datetime.utcnow()
    db.commit()


def create_sync_batch(db: Session, course_id: str, source_root: Path, trigger_source: str = "sync") -> IngestionBatch:
    batch = IngestionBatch(course_id=course_id, source_root=str(source_root), trigger_source=trigger_source, status="queued")
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def summarize_batch(batch: IngestionBatch) -> dict:
    stats = batch.stats or {}
    return {
        "batch_id": batch.id,
        "state": batch.status,
        "trigger_source": batch.trigger_source,
        "source_root": batch.source_root,
        "total_files": batch.total_files,
        "processed_files": batch.processed_files,
        "success_count": batch.success_count,
        "failure_count": batch.failure_count,
        "skipped_count": batch.skipped_count,
        "coverage_by_source_type": stats.get("coverage_by_source_type", {}),
        "errors": stats.get("errors", []),
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
    }


def choose_llm_graph_chunks(chunks: list[Chunk], limit: int = 3) -> set[str]:
    priority = {"markdown": 4, "pdf_page": 4, "doc_section": 4, "slide": 4, "text": 3, "html": 3, "ocr": 3, "code": 0, "output": 0}
    ranked = sorted(
        chunks,
        key=lambda chunk: (
            priority.get(chunk.metadata_json.get("content_kind", "text"), 2),
            len(chunk.content),
            1 if chunk.source_type == "notebook" else 2,
        ),
        reverse=True,
    )
    return {chunk.id for chunk in ranked[:limit]}


def get_batch_status(db: Session, batch_id: str) -> dict | None:
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        return None
    return summarize_batch(batch)


def create_or_update_document(
    db: Session,
    course: Course,
    source_path: Path,
    title: str,
    source_type: str,
    checksum: str,
    tags: list[str] | None = None,
    difficulty: str | None = None,
) -> tuple[Document, int]:
    document = db.scalar(select(Document).where(Document.course_id == course.id, Document.source_path == str(source_path)))
    if document is None:
        document = Document(
            course_id=course.id,
            title=title,
            source_path=str(source_path),
            source_type=source_type,
            checksum=checksum,
            tags=tags or [],
            difficulty=difficulty,
            visibility="private",
        )
        db.add(document)
        db.flush()
        version_number = 1
    else:
        document.checksum = checksum
        document.title = title
        document.source_type = source_type
        document.tags = tags or document.tags
        document.difficulty = difficulty or document.difficulty
        version_number = (db.scalar(select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == document.id)) or 0) + 1
        db.query(DocumentVersion).filter(DocumentVersion.document_id == document.id).update({"is_active": False})
        db.query(Chunk).filter(Chunk.document_id == document.id).update({"is_active": False})
    db.commit()
    db.refresh(document)
    return document, version_number


async def ingest_file(
    db: Session,
    source_path: Path,
    trigger_source: str = "upload",
    existing_job_id: str | None = None,
    batch_id: str | None = None,
    course_id: str | None = None,
) -> dict:
    job = db.get(IngestionJob, existing_job_id) if existing_job_id else None
    course = resolve_course(db, job.course_id if job is not None else course_id)
    course_paths = get_course_paths(course.name)
    checksum = compute_checksum(source_path)

    existing_document = db.scalar(select(Document).where(Document.course_id == course.id, Document.source_path == str(source_path)))
    active_version = None
    if existing_document is not None:
        active_version = db.scalar(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == existing_document.id, DocumentVersion.is_active.is_(True))
            .order_by(DocumentVersion.version.desc())
        )

    if job is None:
        job = create_job(db, course.id, existing_document.id if existing_document else None, trigger_source, batch_id=batch_id, source_path=str(source_path))
    else:
        job.course_id = course.id
        job.batch_id = batch_id
        job.source_path = str(source_path)
        job.trigger_source = trigger_source
        db.commit()

    if active_version and active_version.checksum == checksum:
        job.document_id = existing_document.id if existing_document else None
        job.status = "skipped"
        job.stats = {
            "chunks": db.query(Chunk).filter(Chunk.document_id == existing_document.id, Chunk.is_active.is_(True)).count() if existing_document else 0,
            "concepts": 0,
            "relations": 0,
            "source_type": existing_document.source_type if existing_document else "unknown",
        }
        db.commit()
        return {
            "job_id": job.id,
            "document_id": existing_document.id if existing_document else "",
            "status": "skipped",
            "stats": job.stats,
            "source_type": existing_document.source_type if existing_document else "unknown",
        }

    set_job_state(db, job, "parsing", batch_id=batch_id)
    storage_path = copy_source_file(source_path, course.name) if course_paths["storage_root"] not in source_path.parents else source_path
    source_type, sections = parse_document(storage_path)
    if not sections:
        raise RuntimeError(f"No readable content extracted from {source_path.name}")

    chapter = derive_chapter(source_path)
    document, version_number = create_or_update_document(
        db=db,
        course=course,
        source_path=source_path,
        title=source_path.stem,
        source_type=source_type,
        checksum=checksum,
        tags=[chapter],
    )
    job.document_id = document.id
    db.commit()

    version = DocumentVersion(
        document_id=document.id,
        version=version_number,
        checksum=checksum,
        storage_path=str(storage_path),
        extracted_path=str(course_paths["ingestion_root"] / f"{document.id}-{version_number}.json"),
        is_active=True,
    )
    db.add(version)
    db.flush()

    extracted_json = course_paths["ingestion_root"] / f"{document.id}-{version_number}.json"
    extracted_json.write_text(json.dumps(sections_to_json(sections), ensure_ascii=False, indent=2), encoding="utf-8")

    set_job_state(db, job, "chunking", batch_id=batch_id)
    chunk_payloads = chunk_sections(sections, chapter=chapter, source_type=source_type)
    created_chunks: list[Chunk] = []
    for payload in chunk_payloads:
        chunk = Chunk(
            course_id=course.id,
            document_id=document.id,
            document_version_id=version.id,
            content=payload["content"],
            snippet=payload["snippet"],
            chapter=payload["chapter"],
            section=payload["section"],
            page_number=payload["page_number"],
            token_count=payload["token_count"],
            source_type=source_type,
            metadata_json=payload["metadata"],
            embedding_status="pending",
        )
        db.add(chunk)
        created_chunks.append(chunk)
    db.flush()

    set_job_state(db, job, "embedding", batch_id=batch_id)
    embedder = EmbeddingProvider()
    vector_store = VectorStore(course_name=course.name)
    embeddings = await embedder.embed_texts([chunk.content for chunk in created_chunks], text_type="document")
    vector_points = []
    for chunk, vector in zip(created_chunks, embeddings):
        chunk.embedding_status = "ready"
        vector_points.append(
            {
                "id": chunk.id,
                "vector": vector,
                "payload": {
                    "course_id": course.id,
                    "document_id": document.id,
                    "document_title": document.title,
                    "source_path": document.source_path,
                    "chapter": chunk.chapter,
                    "section": chunk.section,
                    "page_number": chunk.page_number,
                    "snippet": chunk.snippet,
                    "source_type": source_type,
                    "version": version.version,
                    "tags": document.tags,
                    "difficulty": document.difficulty,
                    "content": chunk.content,
                    "content_kind": chunk.metadata_json.get("content_kind"),
                },
            }
        )
    vector_store.upsert(vector_points)

    set_job_state(db, job, "extracting_graph", batch_id=batch_id)
    concept_count = 0
    relation_count = 0
    llm_chunk_ids = choose_llm_graph_chunks(created_chunks, limit=3)
    for chunk in created_chunks:
        created, relations = await upsert_concepts_from_chunk(db, course.id, chunk, use_llm=chunk.id in llm_chunk_ids)
        concept_count += created
        relation_count += relations

    job.status = "completed"
    job.error_message = None
    job.stats = {
        "chunks": len(created_chunks),
        "concepts": concept_count,
        "relations": relation_count,
        "source_type": source_type,
        "chapter": chapter,
        "version": version.version,
    }
    db.commit()
    db.refresh(job)
    return {
        "job_id": job.id,
        "document_id": document.id,
        "status": job.status,
        "stats": job.stats,
        "source_type": source_type,
        "concept_cards": get_concept_cards(db, course.id),
        "graph": get_graph_payload(db, course.id),
    }


async def run_batch_ingestion(batch_id: str) -> dict:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        batch = session.get(IngestionBatch, batch_id)
        if batch is None:
            raise RuntimeError(f"Batch {batch_id} not found")
        root = Path(batch.source_root)
        if not root.exists():
            batch.status = "failed"
            batch.last_error = f"Source root not found: {root}"
            batch.completed_at = datetime.utcnow()
            session.commit()
            return summarize_batch(batch)

        files = collect_source_documents(root)
        batch.total_files = len(files)
        batch.processed_files = 0
        batch.success_count = 0
        batch.failure_count = 0
        batch.skipped_count = 0
        batch.status = "queued"
        batch.started_at = datetime.utcnow()
        batch.completed_at = None
        coverage: Counter[str] = Counter()
        errors: list[dict] = []
        session.commit()

        course = resolve_course(session, batch.course_id)
        for path in files:
            job = create_job(
                session,
                course_id=course.id,
                document_id=None,
                trigger_source=batch.trigger_source,
                batch_id=batch.id,
                source_path=str(path),
            )
            try:
                result = await ingest_file(
                    session,
                    path,
                    trigger_source=batch.trigger_source,
                    existing_job_id=job.id,
                    batch_id=batch.id,
                )
                coverage[result.get("source_type", "unknown")] += 1
                if result["status"] == "skipped":
                    batch.skipped_count += 1
                else:
                    batch.success_count += 1
            except Exception as exc:
                failed_job = session.get(IngestionJob, job.id)
                if failed_job is not None:
                    failed_job.status = "failed"
                    failed_job.error_message = str(exc)
                batch.failure_count += 1
                batch.last_error = str(exc)
                errors.append({"source_path": str(path), "message": str(exc)})
                session.commit()
            finally:
                batch = session.get(IngestionBatch, batch_id)
                if batch is None:
                    break
                batch.processed_files += 1
                batch.stats = {"coverage_by_source_type": dict(coverage), "errors": errors}
                session.commit()

        batch = session.get(IngestionBatch, batch_id)
        if batch is None:
            raise RuntimeError(f"Batch {batch_id} disappeared")
        batch.stats = {"coverage_by_source_type": dict(coverage), "errors": errors, "degraded_mode": is_degraded_mode()}
        if batch.failure_count == batch.total_files and batch.total_files > 0:
            batch.status = "failed"
        elif batch.failure_count > 0:
            batch.status = "partial_failed"
        else:
            batch.status = "completed"
        batch.completed_at = datetime.utcnow()
        session.commit()
        return summarize_batch(batch)
    finally:
        session.close()


async def run_ingestion_job(job_id: str, source_path: Path, trigger_source: str = "upload") -> dict:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        return await ingest_file(session, source_path, trigger_source=trigger_source, existing_job_id=job_id)
    except Exception as exc:
        job = session.get(IngestionJob, job_id)
        if job:
            job.status = "failed"
            job.error_message = str(exc)
            session.commit()
        raise
    finally:
        session.close()
