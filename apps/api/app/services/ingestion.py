from __future__ import annotations

import json
import asyncio
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from threading import Lock

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.utils import source_type_from_path
from app.models import Chunk, Concept, Course, Document, DocumentVersion, IngestionBatch, IngestionCompensationLog, IngestionJob
from app.services.chunking import chunk_sections
from app.services.concept_graph import get_concept_cards, get_graph_payload, graph_extraction_provider, rebuild_course_graph
from app.services.embeddings import EmbeddingProvider, is_degraded_mode
from app.services.ingestion_logs import emit_ingestion_log
from app.services.parsers import derive_chapter, parse_document, sections_to_json
from app.services.storage import compute_checksum, copy_source_file
from app.services.vector_store import VectorStore


ALLOWED_SUFFIXES = {".pdf", ".ipynb", ".md", ".markdown", ".txt", ".docx", ".pptx", ".ppt", ".png", ".jpg", ".jpeg", ".bmp", ".html", ".htm"}
EXCLUDED_PARTS = {"output", "tmp", "scripts", ".ipynb_checkpoints", "__pycache__"}
IGNORED_NAMES = {".ds_store"}
TERMINAL_STATES = {"completed", "failed", "partial_failed", "skipped"}
_SOURCE_PATH_LOCKS: OrderedDict[str, asyncio.Lock] = OrderedDict()
_SOURCE_PATH_LOCKS_GUARD = Lock()
_MAX_SOURCE_PATH_LOCKS = 256


def normalized_source_path(path: Path) -> str:
    return str(path.resolve()).lower()


def source_path_lock(path: Path) -> asyncio.Lock:
    key = normalized_source_path(path)
    with _SOURCE_PATH_LOCKS_GUARD:
        if key not in _SOURCE_PATH_LOCKS:
            _SOURCE_PATH_LOCKS[key] = asyncio.Lock()
        else:
            _SOURCE_PATH_LOCKS.move_to_end(key)
        while len(_SOURCE_PATH_LOCKS) > _MAX_SOURCE_PATH_LOCKS:
            _SOURCE_PATH_LOCKS.popitem(last=False)
        return _SOURCE_PATH_LOCKS[key]


def active_batch_for_course(db: Session, course_id: str) -> IngestionBatch | None:
    return db.scalar(
        select(IngestionBatch)
        .where(IngestionBatch.course_id == course_id, IngestionBatch.status.notin_(TERMINAL_STATES))
        .order_by(IngestionBatch.created_at.desc())
    )


def create_vector_compensation_log(
    db: Session,
    *,
    course_id: str,
    job_id: str | None,
    operation: str,
    vector_ids: list[str],
    payload_json: dict | None = None,
) -> IngestionCompensationLog:
    log = IngestionCompensationLog(
        course_id=course_id,
        job_id=job_id,
        operation=operation,
        vector_ids=vector_ids,
        payload_json=payload_json or {},
        status="pending",
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


def mark_vector_compensation_log(db: Session, log_id: str, status: str, error: str | None = None) -> None:
    log = db.get(IngestionCompensationLog, log_id)
    if log is None:
        return
    log.status = status
    log.error_message = error
    db.commit()


def process_pending_vector_compensations(db: Session) -> int:
    pending = db.scalars(
        select(IngestionCompensationLog).where(IngestionCompensationLog.status == "pending").order_by(IngestionCompensationLog.created_at.asc())
    ).all()
    processed = 0
    for log in pending:
        course = db.get(Course, log.course_id)
        if course is None:
            log.status = "failed"
            log.error_message = "Course no longer exists"
            continue
        try:
            vector_store = VectorStore(course_name=course.name)
            if log.operation == "upsert":
                active_ids = set(
                    db.scalars(
                        select(Chunk.id).where(
                            Chunk.course_id == log.course_id,
                            Chunk.id.in_(log.vector_ids),
                            Chunk.is_active.is_(True),
                        )
                    ).all()
                )
                inactive_ids = [vector_id for vector_id in log.vector_ids if vector_id not in active_ids]
                if inactive_ids:
                    vector_store.delete(inactive_ids)
            elif log.operation == "delete":
                vector_store.delete(log.vector_ids)
            elif log.operation == "restore":
                points = log.payload_json.get("points", [])
                if points:
                    vector_store.upsert(points)
            log.status = "completed"
            log.error_message = None
            processed += 1
        except Exception as exc:
            log.status = "failed"
            log.error_message = str(exc)
    if pending:
        db.commit()
    return processed


def finalize_graph_generation_failure(session: Session, batch_id: str, exc: Exception, stats: dict) -> dict:
    batch = session.get(IngestionBatch, batch_id)
    if batch is None:
        raise RuntimeError(f"Batch {batch_id} disappeared") from exc
    graph_stats = {
        "graph_rebuilt": False,
        "graph_nodes": 0,
        "graph_edges": 0,
        "concepts": 0,
        "relations": 0,
        "graph_extraction_provider": graph_extraction_provider(),
        "graph_error": f"{type(exc).__name__}: {exc}",
    }
    batch.stats = {**stats, **graph_stats}
    batch.status = "partial_failed" if batch.success_count > 0 else "failed"
    batch.last_error = f"Graph generation failed: {exc}"
    batch.completed_at = datetime.utcnow()
    session.commit()
    emit_ingestion_log(batch_id, "graph_failed", batch.last_error, **graph_stats)
    emit_ingestion_log(
        batch_id,
        "batch_partial_failed" if batch.status == "partial_failed" else "batch_failed",
        f"Batch {batch.status}: graph generation failed after {batch.success_count} file(s) succeeded",
        state=batch.status,
        processed_files=batch.processed_files,
        total_files=batch.total_files,
        success_count=batch.success_count,
        failure_count=batch.failure_count,
        skipped_count=batch.skipped_count,
    )
    return summarize_batch(batch)


async def rebuild_course_graph_for_batch(session: Session, course_id: str, batch_id: str) -> dict:
    try:
        return await rebuild_course_graph(session, course_id, batch_id=batch_id)
    except TypeError as exc:
        if "unexpected keyword argument 'batch_id'" not in str(exc):
            raise
        return await rebuild_course_graph(session, course_id)


def embedding_audit_payload(provider: str, external_called: bool, fallback_reason: str | None, vector_count: int) -> dict:
    return {
        "embedding_provider": provider,
        "embedding_external_called": external_called,
        "embedding_fallback_reason": fallback_reason,
        "embedding_vector_count": vector_count,
        "graph_embedding_external_called": False,
        "graph_extraction_provider": graph_extraction_provider(),
    }


def configured_embedding_provider() -> str:
    settings = get_settings()
    if settings.openai_api_key:
        return "openai_compatible"
    return "fake" if settings.enable_model_fallback else "unavailable"


def embedding_fallback_reason() -> str | None:
    settings = get_settings()
    if not settings.openai_api_key:
        return "missing_openai_api_key"
    return None


def emit_model_audit_log(batch_id: str) -> None:
    settings = get_settings()
    embedding_provider = configured_embedding_provider()
    fallback_reason = embedding_fallback_reason()
    fallback_method = "deterministic_local_hash_embedding" if embedding_provider == "fake" else None
    graph_provider = graph_extraction_provider()
    emit_ingestion_log(
        batch_id,
        "model_audit",
        f"Embedding model: {settings.embedding_model} via {embedding_provider}" + (f" fallback={fallback_method}" if fallback_method else ""),
        api_provider="openai_compatible",
        api_base_url=settings.openai_base_url,
        api_resolve_ip=settings.openai_resolve_ip,
        fallback_enabled=settings.enable_model_fallback,
        embedding_provider=embedding_provider,
        embedding_model=settings.embedding_model,
        embedding_external_called=False,
        embedding_fallback_reason=fallback_reason,
        embedding_fallback_method=fallback_method,
        graph_embedding_external_called=False,
        graph_extraction_provider=graph_provider,
        graph_extraction_model=settings.chat_model if graph_provider == "openai_compatible_chat" else graph_provider,
    )


def get_course_paths(course_name: str) -> dict[str, Path]:
    settings = get_settings()
    return settings.course_paths_for_name(course_name)


def ensure_course_directories(course_name: str) -> dict[str, Path]:
    paths = get_course_paths(course_name)
    for key in ("course_root", "storage_root", "ingestion_root"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def summarize_course(db: Session, course: Course) -> dict:
    paths = get_course_paths(course.name)
    storage_root = paths["storage_root"]
    document_count = len(collect_source_documents(storage_root)) if storage_root.exists() else db.query(Document).filter(Document.course_id == course.id, Document.is_active.is_(True)).count()
    concept_count = db.query(Concept).filter(Concept.course_id == course.id).count()
    return {
        "id": course.id,
        "name": course.name,
        "description": course.description,
        "source_root": str(storage_root),
        "storage_root": str(storage_root),
        "document_count": document_count,
        "concept_count": concept_count,
        "degraded_mode": is_degraded_mode(),
    }


def create_course_space(db: Session, name: str, description: str | None = None) -> Course:
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("Course name cannot be empty")
    paths = ensure_course_directories(normalized_name)
    storage_root = paths["storage_root"]
    course = db.scalar(select(Course).where(Course.name == normalized_name))
    if course is None:
        course = Course(name=normalized_name, description=description, source_root=str(storage_root))
        db.add(course)
        db.commit()
        db.refresh(course)
        return course
    if description is not None:
        course.description = description
    course.source_root = str(storage_root)
    db.commit()
    db.refresh(course)
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
    if course.source_root != str(paths["storage_root"]):
        course.source_root = str(paths["storage_root"])
        db.commit()
        db.refresh(course)
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
    if batch_id:
        emit_ingestion_log(batch_id, "job_state", f"{Path(job.source_path or '').name or job.id}: {state}", job_id=job.id, source_path=job.source_path, state=state)


def create_sync_batch(db: Session, course_id: str, root: Path, trigger_source: str = "sync") -> IngestionBatch:
    active = active_batch_for_course(db, course_id)
    if active is not None:
        return active
    batch = IngestionBatch(course_id=course_id, source_root=str(root), trigger_source=trigger_source, status="queued")
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return batch


def create_uploaded_files_batch(db: Session, course_id: str, files: list[Path], force: bool = False) -> IngestionBatch:
    active = active_batch_for_course(db, course_id)
    if active is not None:
        return active
    storage_batch_root = str(files[0].parent) if files else "storage files"
    batch = IngestionBatch(course_id=course_id, source_root=storage_batch_root, trigger_source="upload", status="queued")
    batch.total_files = len(files)
    batch.stats = {"uploaded_files": [str(path) for path in files], "coverage_by_source_type": {}, "errors": [], "force": force}
    db.add(batch)
    db.flush()
    if force:
        for path in files:
            path_string = str(path)
            document = db.scalar(select(Document).where(Document.course_id == course_id, Document.source_path == path_string))
            job = db.scalar(
                select(IngestionJob)
                .where(IngestionJob.course_id == course_id, IngestionJob.source_path == path_string)
                .order_by(IngestionJob.updated_at.desc())
            )
            if job is None:
                job = IngestionJob(
                    course_id=course_id,
                    document_id=document.id if document else None,
                    batch_id=batch.id,
                    source_path=path_string,
                    trigger_source="upload",
                    status="queued",
                    stats={"force_reparse": True},
                )
                db.add(job)
            else:
                job.document_id = document.id if document else job.document_id
                job.batch_id = batch.id
                job.source_path = path_string
                job.trigger_source = "upload"
                job.status = "queued"
                job.error_message = None
                job.stats = {"force_reparse": True}
    db.commit()
    db.refresh(batch)
    return batch


def register_uploaded_file(db: Session, course: Course, source_path: Path) -> tuple[Document, IngestionJob]:
    checksum = compute_checksum(source_path)
    document = db.scalar(
        select(Document)
        .where(Document.course_id == course.id, Document.source_path == str(source_path))
        .with_for_update()
    )
    if document is None:
        document = Document(
            course_id=course.id,
            title=source_path.stem,
            source_path=str(source_path),
            source_type=source_type_from_path(source_path),
            checksum=checksum,
            tags=[],
            visibility="private",
            is_active=True,
        )
        db.add(document)
        db.flush()
    else:
        document.title = source_path.stem
        document.source_type = source_type_from_path(source_path)
        document.checksum = checksum
        document.is_active = True

    job = db.scalar(
        select(IngestionJob)
        .where(IngestionJob.course_id == course.id, IngestionJob.source_path == str(source_path))
        .order_by(IngestionJob.updated_at.desc())
        .with_for_update()
    )
    if job is None or job.status not in {"queued", "failed", "skipped"}:
        job = IngestionJob(
            course_id=course.id,
            document_id=document.id,
            source_path=str(source_path),
            trigger_source="upload",
            status="queued",
        )
        db.add(job)
    else:
        job.document_id = document.id
        job.status = "queued"
        job.error_message = None
        job.stats = {}
    db.commit()
    db.refresh(document)
    db.refresh(job)
    return document, job


def summarize_batch(batch: IngestionBatch) -> dict:
    stats = batch.stats or {}
    graph_stats = {
        key: value
        for key, value in stats.items()
        if key.startswith("graph_") or key in {"concepts", "relations"}
    }
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
        "graph_stats": graph_stats,
        "started_at": batch.started_at,
        "completed_at": batch.completed_at,
    }


def get_batch_status(db: Session, batch_id: str) -> dict | None:
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:
        return None
    return summarize_batch(batch)


def finalize_interrupted_batches() -> int:
    from app.db import SessionLocal

    finalized: list[str] = []
    now = datetime.utcnow()
    with SessionLocal() as session:
        process_pending_vector_compensations(session)
        batches = session.scalars(select(IngestionBatch).where(IngestionBatch.status.notin_(TERMINAL_STATES))).all()
        for batch in batches:
            batch.status = "failed"
            batch.last_error = "Interrupted because the API process restarted before this batch reached a terminal state"
            batch.completed_at = now
            jobs = session.scalars(
                select(IngestionJob).where(
                    IngestionJob.batch_id == batch.id,
                    IngestionJob.status.notin_(TERMINAL_STATES),
                )
            ).all()
            for job in jobs:
                job.status = "failed"
                job.error_message = batch.last_error
            finalized.append(batch.id)
        session.commit()

    for batch_id in finalized:
        emit_ingestion_log(
            batch_id,
            "batch_failed",
            "Batch failed because the API process restarted before it reached a terminal state",
            state="failed",
        )
    return len(finalized)


def remove_course_file(db: Session, course: Course, source_path: str) -> bool:
    document = db.scalar(select(Document).where(Document.course_id == course.id, Document.source_path == source_path))
    jobs = db.scalars(select(IngestionJob).where(IngestionJob.course_id == course.id, IngestionJob.source_path == source_path)).all()
    removed = False
    file_to_delete: Path | None = None

    if document is not None:
        document.is_active = False
        db.query(DocumentVersion).filter(DocumentVersion.document_id == document.id).update({"is_active": False})
        db.query(Chunk).filter(Chunk.document_id == document.id).update({"is_active": False})
        removed = True

    for job in jobs:
        if job.status not in TERMINAL_STATES:
            job.status = "skipped"
            job.error_message = "Removed by user before parsing completed"
        job.stats = {**(job.stats or {}), "removed": True}
        removed = True

    if not jobs:
        tombstone = IngestionJob(
            course_id=course.id,
            document_id=document.id if document else None,
            source_path=source_path,
            trigger_source="remove",
            status="skipped",
            error_message="Removed by user",
            stats={"removed": True},
        )
        db.add(tombstone)
        removed = True

    course_paths = get_course_paths(course.name)
    storage_root = course_paths["storage_root"].resolve()
    resolved_path = Path(source_path).resolve()
    if resolved_path.exists() and resolved_path.is_file() and (resolved_path == storage_root or storage_root in resolved_path.parents):
        file_to_delete = resolved_path
        removed = True

    if removed:
        db.commit()
        if file_to_delete is not None:
            try:
                file_to_delete.unlink()
            except OSError:
                pass
    return removed


def create_or_update_document(
    db: Session,
    course: Course,
    source_path: Path,
    title: str,
    source_type: str,
    checksum: str,
    tags: list[str] | None = None,
    difficulty: str | None = None,
) -> tuple[Document, int, list[str]]:
    document = db.scalar(select(Document).where(Document.course_id == course.id, Document.source_path == str(source_path)))
    stale_chunk_ids: list[str] = []
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
        stale_chunk_ids = [
            chunk_id
            for (chunk_id,) in db.query(Chunk.id)
            .filter(Chunk.document_id == document.id, Chunk.is_active.is_(True))
            .all()
        ]
        document.is_active = True
        document.title = title
        document.source_type = source_type
        document.tags = tags or document.tags
        document.difficulty = difficulty or document.difficulty
        version_number = (db.scalar(select(func.max(DocumentVersion.version)).where(DocumentVersion.document_id == document.id)) or 0) + 1
    db.flush()
    return document, version_number, stale_chunk_ids


async def ingest_file(
    db: Session,
    source_path: Path,
    trigger_source: str = "upload",
    existing_job_id: str | None = None,
    batch_id: str | None = None,
    course_id: str | None = None,
    rebuild_graph: bool = True,
    force: bool = False,
) -> dict:
    async with source_path_lock(source_path):
        return await _ingest_file_locked(
            db=db,
            source_path=source_path,
            trigger_source=trigger_source,
            existing_job_id=existing_job_id,
            batch_id=batch_id,
            course_id=course_id,
            rebuild_graph=rebuild_graph,
            force=force,
        )


async def _ingest_file_locked(
    db: Session,
    source_path: Path,
    trigger_source: str = "upload",
    existing_job_id: str | None = None,
    batch_id: str | None = None,
    course_id: str | None = None,
    rebuild_graph: bool = True,
    force: bool = False,
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

    if active_version and active_version.checksum == checksum and not force:
        chunk_count = db.query(Chunk).filter(Chunk.document_id == existing_document.id, Chunk.is_active.is_(True)).count() if existing_document else 0
        job.document_id = existing_document.id if existing_document else None
        job.status = "skipped"
        job.stats = {
            "chunks": chunk_count,
            "concepts": 0,
            "relations": 0,
            "source_type": existing_document.source_type if existing_document else "unknown",
            "graph_rebuilt": False,
            **embedding_audit_payload(configured_embedding_provider(), False, "unchanged_checksum", 0),
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

    chapter = derive_chapter(source_path, course_name=course.name)
    document, version_number, stale_chunk_ids = create_or_update_document(
        db=db,
        course=course,
        source_path=source_path,
        title=source_path.stem,
        source_type=source_type,
        checksum=checksum,
        tags=[chapter],
    )
    job.document_id = document.id
    vector_store = VectorStore(course_name=course.name)

    version = DocumentVersion(
        document_id=document.id,
        version=version_number,
        checksum=checksum,
        storage_path=str(storage_path),
        extracted_path=str(course_paths["ingestion_root"] / f"{document.id}-{version_number}.json"),
        is_active=False,
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
            is_active=False,
        )
        db.add(chunk)
        created_chunks.append(chunk)
    db.flush()

    set_job_state(db, job, "embedding", batch_id=batch_id)
    embedder = EmbeddingProvider()
    embedding_result = await embedder.embed_texts_with_meta([chunk.content for chunk in created_chunks], text_type="document")
    embeddings = embedding_result.vectors
    emit_ingestion_log(
        batch_id or job.id,
        "embedding_audit",
        f"Embedding provider: {embedding_result.provider}, vectors: {len(embeddings)}",
        provider=embedding_result.provider,
        model=embedder.settings.embedding_model,
        external_called=embedding_result.external_called,
        fallback_reason=embedding_result.fallback_reason,
        vector_count=len(embeddings),
    )
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
    db.commit()

    new_chunk_ids = [chunk.id for chunk in created_chunks]
    upsert_log = create_vector_compensation_log(
        db,
        course_id=course.id,
        job_id=job.id,
        operation="upsert",
        vector_ids=new_chunk_ids,
    )
    try:
        vector_store.upsert(vector_points)
    except Exception as exc:
        mark_vector_compensation_log(db, upsert_log.id, "failed", str(exc))
        raise

    try:
        db.query(DocumentVersion).filter(
            DocumentVersion.document_id == document.id,
            DocumentVersion.id != version.id,
        ).update({"is_active": False}, synchronize_session=False)
        db.query(Chunk).filter(
            Chunk.document_id == document.id,
            Chunk.id.notin_(new_chunk_ids),
        ).update({"is_active": False}, synchronize_session=False)
        version.is_active = True
        for chunk in created_chunks:
            chunk.is_active = True
        document.checksum = checksum
        document.is_active = True
        db.commit()
    except Exception as exc:
        db.rollback()
        try:
            vector_store.delete(new_chunk_ids)
        finally:
            mark_vector_compensation_log(db, upsert_log.id, "failed", f"DB activation failed after upsert; compensated new vectors: {exc}")
        raise
    mark_vector_compensation_log(db, upsert_log.id, "completed")

    if stale_chunk_ids:
        stale_points = []
        try:
            stale_points = vector_store.get_points(stale_chunk_ids)
        except Exception:
            stale_points = []
        delete_log = create_vector_compensation_log(
            db,
            course_id=course.id,
            job_id=job.id,
            operation="delete",
            vector_ids=stale_chunk_ids,
            payload_json={"points": stale_points},
        )
        try:
            vector_store.delete(stale_chunk_ids)
            mark_vector_compensation_log(db, delete_log.id, "completed")
        except Exception as exc:
            mark_vector_compensation_log(db, delete_log.id, "failed", str(exc))

    set_job_state(db, job, "extracting_graph", batch_id=batch_id)
    graph_stats = (
        await rebuild_course_graph(db, course.id)
        if rebuild_graph
        else {
            "graph_rebuilt": False,
            "concepts": 0,
            "relations": 0,
            "graph_nodes": 0,
            "graph_edges": 0,
            "graph_extraction_provider": graph_extraction_provider(),
        }
    )

    job.status = "completed"
    job.error_message = None
    job.stats = {
        "chunks": len(created_chunks),
        "concepts": graph_stats["concepts"],
        "relations": graph_stats["relations"],
        "source_type": source_type,
        "chapter": chapter,
        "version": version.version,
        **graph_stats,
        **embedding_audit_payload(
            embedding_result.provider,
            embedding_result.external_called,
            embedding_result.fallback_reason,
            len(embeddings),
        ),
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
            batch.last_error = f"Storage root not found: {root}"
            batch.completed_at = datetime.utcnow()
            session.commit()
            emit_ingestion_log(batch_id, "batch_failed", batch.last_error)
            return summarize_batch(batch)

        files = collect_source_documents(root)
        emit_model_audit_log(batch_id)
        emit_ingestion_log(batch_id, "batch_started", f"Scanning course storage: {root}")
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
        emit_ingestion_log(batch_id, "batch_files", f"Found {len(files)} files to parse", total_files=len(files))
        for index, path in enumerate(files, start=1):
            emit_ingestion_log(batch_id, "file_started", f"[{index}/{len(files)}] Parsing {path.name}", source_path=str(path), processed_files=batch.processed_files, total_files=batch.total_files)
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
                    rebuild_graph=False,
                )
                coverage[result.get("source_type", "unknown")] += 1
                if result["status"] == "skipped":
                    batch.skipped_count += 1
                    emit_ingestion_log(batch_id, "file_skipped", f"Skipped {path.name}", source_path=str(path))
                else:
                    batch.success_count += 1
                    emit_ingestion_log(batch_id, "file_completed", f"Completed {path.name}", source_path=str(path), stats=result.get("stats", {}))
            except Exception as exc:
                session.rollback()
                failed_job = session.get(IngestionJob, job.id)
                if failed_job is not None:
                    failed_job.status = "failed"
                    failed_job.error_message = str(exc)
                batch = session.get(IngestionBatch, batch_id)
                if batch is not None:
                    batch.failure_count += 1
                    batch.last_error = str(exc)
                errors.append({"source_path": str(path), "message": str(exc)})
                emit_ingestion_log(batch_id, "file_failed", f"Failed {path.name}: {exc}", source_path=str(path), error=str(exc))
                session.commit()
            finally:
                batch = session.get(IngestionBatch, batch_id)
                if batch is None:
                    break
                batch.processed_files += 1
                batch.stats = {"coverage_by_source_type": dict(coverage), "errors": errors}
                emit_ingestion_log(
                    batch_id,
                    "batch_progress",
                    f"Progress {batch.processed_files}/{batch.total_files}",
                    processed_files=batch.processed_files,
                    total_files=batch.total_files,
                    success_count=batch.success_count,
                    failure_count=batch.failure_count,
                    skipped_count=batch.skipped_count,
                )
                session.commit()

        batch = session.get(IngestionBatch, batch_id)
        if batch is None:
            raise RuntimeError(f"Batch {batch_id} disappeared")
        if batch.success_count > 0:
            settings = get_settings()
            batch.status = "extracting_graph"
            session.commit()
            emit_ingestion_log(
                batch_id,
                "batch_graph_started",
                "Generating course graph",
                processed_files=batch.processed_files,
                total_files=batch.total_files,
                success_count=batch.success_count,
                failure_count=batch.failure_count,
                skipped_count=batch.skipped_count,
                graph_extraction_chunk_limit=settings.graph_extraction_chunk_limit,
                graph_extraction_chunks_per_document=settings.graph_extraction_chunks_per_document,
            )
            try:
                graph_stats = await rebuild_course_graph_for_batch(session, batch.course_id, batch_id)
            except Exception as exc:
                session.rollback()
                return finalize_graph_generation_failure(
                    session,
                    batch_id,
                    exc,
                    {
                        "coverage_by_source_type": dict(coverage),
                        "errors": errors,
                        "degraded_mode": is_degraded_mode(),
                    },
                )
        else:
            graph_stats = {
                "graph_rebuilt": False,
                "graph_nodes": 0,
                "graph_edges": 0,
                "concepts": 0,
                "relations": 0,
                "graph_extraction_provider": graph_extraction_provider(),
            }
        batch.stats = {
            "coverage_by_source_type": dict(coverage),
            "errors": errors,
            "degraded_mode": is_degraded_mode(),
            **graph_stats,
        }
        if batch.skipped_count == batch.total_files and batch.total_files > 0:
            batch.status = "skipped"
            terminal_event = "batch_skipped"
        elif batch.failure_count == batch.total_files and batch.total_files > 0:
            batch.status = "failed"
            terminal_event = "batch_failed"
        elif graph_stats.get("graph_llm_failed_chunks", 0) > 0:
            batch.status = "partial_failed"
            terminal_event = "batch_partial_failed"
            batch.last_error = f"Graph extraction failed for {graph_stats['graph_llm_failed_chunks']} chunk(s)"
        elif batch.failure_count > 0:
            batch.status = "partial_failed"
            terminal_event = "batch_partial_failed"
        else:
            batch.status = "completed"
            terminal_event = "batch_completed"
        batch.completed_at = datetime.utcnow()
        session.commit()
        emit_ingestion_log(batch_id, "graph_rebuilt", f"Graph rebuilt: {graph_stats.get('graph_nodes', 0)} nodes, {graph_stats.get('graph_edges', 0)} edges", **graph_stats)
        emit_ingestion_log(batch_id, terminal_event, f"Batch {batch.status}: {batch.success_count} succeeded, {batch.failure_count} failed, {batch.skipped_count} skipped")
        return summarize_batch(batch)
    finally:
        session.close()


async def run_uploaded_files_ingestion(batch_id: str, file_paths: list[str], force: bool = False) -> dict:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        batch = session.get(IngestionBatch, batch_id)
        if batch is None:
            raise RuntimeError(f"Batch {batch_id} not found")
        files = [Path(path) for path in file_paths]
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
        emit_model_audit_log(batch_id)
        emit_ingestion_log(batch_id, "batch_started", f"Parsing {len(files)} files" + (" with force reparse" if force else ""), total_files=len(files), force=force)
        for index, path in enumerate(files, start=1):
            emit_ingestion_log(batch_id, "file_started", f"[{index}/{len(files)}] Parsing {path.name}", source_path=str(path), processed_files=batch.processed_files, total_files=batch.total_files)
            job = session.scalar(
                select(IngestionJob)
                .where(IngestionJob.course_id == course.id, IngestionJob.source_path == str(path))
                .order_by(IngestionJob.updated_at.desc())
            )
            if job is None:
                document = session.scalar(select(Document).where(Document.course_id == course.id, Document.source_path == str(path)))
                job = create_job(
                    session,
                    course_id=course.id,
                    document_id=document.id if document else None,
                    trigger_source="upload",
                    batch_id=batch.id,
                    source_path=str(path),
                )
            else:
                job.batch_id = batch.id
                job.trigger_source = "upload"
                job.status = "queued"
                job.error_message = None
                session.commit()
            try:
                if not path.exists():
                    raise RuntimeError(f"File not found: {path}")
                result = await ingest_file(
                    session,
                    path,
                    trigger_source="upload",
                    existing_job_id=job.id,
                    batch_id=batch.id,
                    course_id=course.id,
                    rebuild_graph=False,
                    force=force,
                )
                coverage[result.get("source_type", "unknown")] += 1
                if result["status"] == "skipped":
                    batch.skipped_count += 1
                    emit_ingestion_log(batch_id, "file_skipped", f"Skipped {path.name}", source_path=str(path))
                else:
                    batch.success_count += 1
                    emit_ingestion_log(batch_id, "file_completed", f"Completed {path.name}", source_path=str(path), stats=result.get("stats", {}))
            except Exception as exc:
                session.rollback()
                failed_job = session.get(IngestionJob, job.id)
                if failed_job is not None:
                    failed_job.status = "failed"
                    failed_job.error_message = str(exc)
                batch = session.get(IngestionBatch, batch_id)
                if batch is not None:
                    batch.failure_count += 1
                    batch.last_error = str(exc)
                errors.append({"source_path": str(path), "message": str(exc)})
                emit_ingestion_log(batch_id, "file_failed", f"Failed {path.name}: {exc}", source_path=str(path), error=str(exc))
                session.commit()
            finally:
                batch = session.get(IngestionBatch, batch_id)
                if batch is None:
                    break
                batch.processed_files += 1
                batch.stats = {"uploaded_files": file_paths, "coverage_by_source_type": dict(coverage), "errors": errors, "force": force}
                emit_ingestion_log(
                    batch_id,
                    "batch_progress",
                    f"Progress {batch.processed_files}/{batch.total_files}",
                    processed_files=batch.processed_files,
                    total_files=batch.total_files,
                    success_count=batch.success_count,
                    failure_count=batch.failure_count,
                    skipped_count=batch.skipped_count,
                )
                session.commit()

        batch = session.get(IngestionBatch, batch_id)
        if batch is None:
            raise RuntimeError(f"Batch {batch_id} disappeared")
        if batch.success_count > 0:
            settings = get_settings()
            batch.status = "extracting_graph"
            session.commit()
            emit_ingestion_log(
                batch_id,
                "batch_graph_started",
                "Generating course graph",
                processed_files=batch.processed_files,
                total_files=batch.total_files,
                success_count=batch.success_count,
                failure_count=batch.failure_count,
                skipped_count=batch.skipped_count,
                graph_extraction_chunk_limit=settings.graph_extraction_chunk_limit,
                graph_extraction_chunks_per_document=settings.graph_extraction_chunks_per_document,
            )
            try:
                graph_stats = await rebuild_course_graph_for_batch(session, batch.course_id, batch_id)
            except Exception as exc:
                session.rollback()
                return finalize_graph_generation_failure(
                    session,
                    batch_id,
                    exc,
                    {
                        "uploaded_files": file_paths,
                        "coverage_by_source_type": dict(coverage),
                        "errors": errors,
                        "force": force,
                        "degraded_mode": is_degraded_mode(),
                    },
                )
        else:
            graph_stats = {
                "graph_rebuilt": False,
                "graph_nodes": 0,
                "graph_edges": 0,
                "concepts": 0,
                "relations": 0,
                "graph_extraction_provider": graph_extraction_provider(),
            }
        batch.stats = {
            "uploaded_files": file_paths,
            "coverage_by_source_type": dict(coverage),
            "errors": errors,
            "force": force,
            "degraded_mode": is_degraded_mode(),
            **graph_stats,
        }
        if batch.skipped_count == batch.total_files and batch.total_files > 0:
            batch.status = "skipped"
            terminal_event = "batch_skipped"
        elif batch.failure_count == batch.total_files and batch.total_files > 0:
            batch.status = "failed"
            terminal_event = "batch_failed"
        elif graph_stats.get("graph_llm_failed_chunks", 0) > 0:
            batch.status = "partial_failed"
            terminal_event = "batch_partial_failed"
            batch.last_error = f"Graph extraction failed for {graph_stats['graph_llm_failed_chunks']} chunk(s)"
        elif batch.failure_count > 0:
            batch.status = "partial_failed"
            terminal_event = "batch_partial_failed"
        else:
            batch.status = "completed"
            terminal_event = "batch_completed"
        batch.completed_at = datetime.utcnow()
        session.commit()
        emit_ingestion_log(batch_id, "graph_rebuilt", f"Graph rebuilt: {graph_stats.get('graph_nodes', 0)} nodes, {graph_stats.get('graph_edges', 0)} edges", **graph_stats)
        emit_ingestion_log(batch_id, terminal_event, f"Batch {batch.status}: {batch.success_count} succeeded, {batch.failure_count} failed, {batch.skipped_count} skipped")
        return summarize_batch(batch)
    finally:
        session.close()


async def run_ingestion_job(job_id: str, source_path: Path, trigger_source: str = "upload") -> dict:
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        return await ingest_file(session, source_path, trigger_source=trigger_source, existing_job_id=job_id)
    except Exception as exc:
        session.rollback()
        job = session.get(IngestionJob, job_id)
        if job:
            job.status = "failed"
            job.error_message = str(exc)
            session.commit()
        raise
    finally:
        session.close()
