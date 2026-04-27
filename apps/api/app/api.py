from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentRun, AgentTraceEvent, QASession
from app.core.config import get_settings
from app.schemas import (
    AgentRequest,
    AgentResponse,
    BatchStartResponse,
    ConceptCard,
    CourseFileSummary,
    CourseCreateRequest,
    CourseSummary,
    DashboardSnapshot,
    DeleteResponse,
    GraphNodeDetail,
    GraphResponse,
    IngestionBatchSummary,
    JobStatusResponse,
    ModelSettingsResponse,
    ModelSettingsUpdate,
    ParseUploadedFilesRequest,
    QARequest,
    QAResponse,
    RefreshResponse,
    SearchRequest,
    SearchResponse,
    SessionMessagesResponse,
    SessionSummary,
    TaskStatusResponse,
    UploadFileResponse,
)
from app.services.concept_graph import get_concept_cards, get_graph_node_detail, get_graph_payload
from app.services.embeddings import is_degraded_mode
from app.services.agent_graph import run_agent, run_to_task_status, stream_agent_events
from app.services.ingestion import (
    create_course_space,
    collect_source_documents,
    create_uploaded_files_batch,
    create_job,
    create_sync_batch,
    get_batch_status,
    list_course_summaries,
    register_uploaded_file,
    resolve_course,
    run_batch_ingestion,
    run_ingestion_job,
    run_uploaded_files_ingestion,
    remove_course_file,
    summarize_course,
)
from app.services.ingestion_logs import TERMINAL_LOG_EVENTS, list_ingestion_logs, subscribe_ingestion_logs, unsubscribe_ingestion_logs
from app.services.retrieval import get_dashboard_snapshot, get_job_status, list_course_files, search_chunks
from app.services.runtime_settings import model_settings_payload, update_model_settings
from app.services.storage import save_upload

router = APIRouter()


def get_requested_course(db: Session, course_id: str | None = None):
    try:
        return resolve_course(db, course_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/health")
def healthcheck() -> dict:
    return {"status": "ok", "degraded_mode": is_degraded_mode()}


@router.get("/settings/model", response_model=ModelSettingsResponse)
def get_model_settings() -> dict:
    return model_settings_payload()


@router.put("/settings/model", response_model=ModelSettingsResponse)
def save_model_settings(request: ModelSettingsUpdate) -> dict:
    return update_model_settings(request.model_dump())


@router.get("/courses", response_model=list[CourseSummary])
def list_courses(db: Session = Depends(get_db)) -> list[dict]:
    return list_course_summaries(db)


@router.post("/courses", response_model=CourseSummary)
def create_course(request: CourseCreateRequest, db: Session = Depends(get_db)) -> dict:
    try:
        course = create_course_space(db, request.name, request.description)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return summarize_course(db, course)


@router.get("/courses/current/dashboard", response_model=DashboardSnapshot)
@router.get("/courses/default/dashboard", response_model=DashboardSnapshot, include_in_schema=False)
def course_dashboard(course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    return get_dashboard_snapshot(db, course.id)


@router.post("/courses/current/refresh", response_model=RefreshResponse)
def refresh_current_course(course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    return {"course_id": course.id, "refreshed_at": datetime.utcnow()}


@router.get("/course-files", response_model=list[CourseFileSummary])
def course_files(course_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    course = get_requested_course(db, course_id)
    return list_course_files(db, course.id)


@router.delete("/course-files")
def delete_course_file(source_path: str, course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    if not remove_course_file(db, course, source_path):
        raise HTTPException(status_code=404, detail="File not found")
    return {"removed": True}


@router.get("/courses/current/graph", response_model=GraphResponse)
@router.get("/courses/default/graph", response_model=GraphResponse, include_in_schema=False)
def course_graph(course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    return get_graph_payload(db, course.id)


@router.get("/graph/chapters/{chapter}", response_model=GraphResponse)
def chapter_graph(chapter: str, course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    return get_graph_payload(db, course.id, chapter=chapter)


@router.get("/graph/nodes/{concept_id}", response_model=GraphNodeDetail)
def graph_node_detail(concept_id: str, course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    payload = get_graph_node_detail(db, course.id, concept_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Concept node not found")
    return payload


@router.get("/concepts", response_model=list[ConceptCard])
def concept_cards(course_id: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    course = get_requested_course(db, course_id)
    return get_concept_cards(db, course.id)


def enqueue_ingestion(job_id: str, source_path: str, trigger_source: str) -> None:
    try:
        from worker_app.tasks import ingest_path

        ingest_path.delay(source_path, trigger_source=trigger_source, job_id=job_id)
    except Exception:
        asyncio.run(run_ingestion_job(job_id, Path(source_path), trigger_source=trigger_source))


def enqueue_batch(batch_id: str) -> None:
    try:
        from worker_app.tasks import ingest_batch

        ingest_batch.delay(batch_id)
    except Exception:
        asyncio.run(run_batch_ingestion(batch_id))


def enqueue_uploaded_batch(batch_id: str, file_paths: list[str], force: bool = False) -> None:
    asyncio.run(run_uploaded_files_ingestion(batch_id, file_paths, force=force))


@router.post("/files/upload", response_model=UploadFileResponse)
async def upload_file(
    course_id: str | None = None,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    course = get_requested_course(db, course_id)
    stored_path = await save_upload(upload, course.name)
    document, job = register_uploaded_file(db, course, stored_path)
    return {"document_id": document.id, "job_id": job.id, "status": "queued", "source_path": str(stored_path)}


@router.post("/ingestion/parse-uploaded-files", response_model=BatchStartResponse)
async def parse_uploaded_files(
    request: ParseUploadedFilesRequest,
    background_tasks: BackgroundTasks,
    course_id: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    course = get_requested_course(db, course_id)
    storage_root = Path(get_settings().course_paths_for_name(course.name)["storage_root"]).resolve()
    requested_paths = request.file_paths or [str(path) for path in collect_source_documents(storage_root)]
    if not requested_paths:
        raise HTTPException(status_code=400, detail="No files found in course storage")
    file_paths = []
    seen_paths: set[Path] = set()
    for raw_path in requested_paths:
        path = Path(raw_path).resolve()
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        if storage_root not in path.parents and path != storage_root:
            raise HTTPException(status_code=400, detail=f"File is outside course storage: {path}")
        if path in seen_paths:
            continue
        seen_paths.add(path)
        file_paths.append(path)
    batch = create_uploaded_files_batch(db, course.id, file_paths, force=request.force)
    background_tasks.add_task(enqueue_uploaded_batch, batch.id, [str(path) for path in file_paths], request.force)
    return {"batch_id": batch.id, "state": "queued"}


@router.post("/ingestion/parse-storage", response_model=BatchStartResponse)
@router.post("/ingestion/sync-source", response_model=BatchStartResponse, include_in_schema=False)
async def parse_storage_directory(background_tasks: BackgroundTasks, course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    root = Path(get_settings().course_paths_for_name(course.name)["storage_root"])
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Storage root not found: {root}")
    batch = create_sync_batch(db, course.id, root, trigger_source="storage")
    background_tasks.add_task(enqueue_batch, batch.id)
    return {"batch_id": batch.id, "state": "queued"}


@router.get("/ingestion/batches/{batch_id}", response_model=IngestionBatchSummary)
def batch_status(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = get_batch_status(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


@router.get("/ingestion/batches/{batch_id}/logs")
async def batch_logs(batch_id: str):
    from app.db import SessionLocal

    with SessionLocal() as session:
        batch_exists = get_batch_status(session, batch_id) is not None

    if not batch_exists:
        async def missing_stream():
            yield f"data: {json.dumps({'timestamp': datetime.utcnow().isoformat(), 'event': 'batch_missing', 'message': 'Batch no longer exists. The local UI state can be cleared.', 'state': 'missing'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(missing_stream(), media_type="text/event-stream")

    async def event_stream():
        emitted: set[str] = set()

        def event_key(item: dict) -> str:
            return str(item.get("log_id") or item.get("synthetic_key") or f"{item.get('timestamp')}:{item.get('event')}:{item.get('message')}")

        def format_new(item: dict) -> str | None:
            key = event_key(item)
            if key in emitted:
                return None
            emitted.add(key)
            return f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

        def batch_snapshot_event() -> dict | None:
            from app.db import SessionLocal

            with SessionLocal() as session:
                snapshot = get_batch_status(session, batch_id)
            if snapshot is None:
                return None
            state = snapshot["state"]
            terminal_events = {
                "completed": "batch_completed",
                "failed": "batch_failed",
                "partial_failed": "batch_partial_failed",
                "skipped": "batch_skipped",
            }
            event = terminal_events.get(state, "batch_status")
            return {
                "synthetic_key": f"snapshot:{state}:{snapshot['processed_files']}:{snapshot['success_count']}:{snapshot['failure_count']}:{snapshot['skipped_count']}",
                "timestamp": datetime.utcnow().isoformat(),
                "event": event,
                "message": f"Batch {state}: {snapshot['processed_files']}/{snapshot['total_files']} processed",
                "state": state,
                "processed_files": snapshot["processed_files"],
                "total_files": snapshot["total_files"],
                "success_count": snapshot["success_count"],
                "failure_count": snapshot["failure_count"],
                "skipped_count": snapshot["skipped_count"],
            }

        history, subscriber = subscribe_ingestion_logs(batch_id)
        try:
            for item in history:
                chunk = format_new(item)
                if chunk:
                    yield chunk
                if item.get("event") in TERMINAL_LOG_EVENTS:
                    return
            while True:
                latest = list_ingestion_logs(batch_id)
                for item in latest:
                    chunk = format_new(item)
                    if chunk:
                        yield chunk
                    if item.get("event") in TERMINAL_LOG_EVENTS:
                        return
                snapshot = batch_snapshot_event()
                if snapshot:
                    chunk = format_new(snapshot)
                    if chunk:
                        yield chunk
                    if snapshot.get("event") in TERMINAL_LOG_EVENTS:
                        return
                try:
                    item = await asyncio.to_thread(subscriber.get, True, 2)
                except Exception:
                    yield ": heartbeat\n\n"
                    continue
                chunk = format_new(item)
                if chunk:
                    yield chunk
                if item.get("event") in TERMINAL_LOG_EVENTS:
                    return
        finally:
            unsubscribe_ingestion_logs(batch_id, subscriber)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str, db: Session = Depends(get_db)) -> dict:
    job = get_job_status(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/search", response_model=SearchResponse)
async def search(request: SearchRequest, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, request.course_id)
    results = await search_chunks(db, course.id, request.query, request.filters, request.top_k)
    return {"query": request.query, "results": results, "degraded_mode": is_degraded_mode()}


@router.post("/qa", response_model=QAResponse)
async def qa(request: QARequest, db: Session = Depends(get_db)) -> dict:
    get_requested_course(db, request.course_id)
    agent_request = AgentRequest(
        question=request.question,
        session_id=request.session_id,
        course_id=request.course_id,
        filters=request.filters,
        top_k=request.top_k,
        history=request.history,
        stream_trace=False,
    )
    return await run_agent(db, agent_request)


@router.post("/qa/stream")
async def qa_stream(request: QARequest, db: Session = Depends(get_db)) -> StreamingResponse:
    get_requested_course(db, request.course_id)
    agent_request = AgentRequest(
        question=request.question,
        session_id=request.session_id,
        course_id=request.course_id,
        filters=request.filters,
        top_k=request.top_k,
        history=request.history,
        stream_trace=True,
    )

    async def event_stream():
        try:
            async for event in stream_agent_events(db, agent_request):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/agent", response_model=AgentResponse)
async def agent_call(request: AgentRequest, db: Session = Depends(get_db)) -> dict:
    get_requested_course(db, request.course_id)
    return await run_agent(db, request)


@router.get("/agent/runs/{run_id}", response_model=TaskStatusResponse)
@router.get("/tasks/{run_id}", response_model=TaskStatusResponse)
def agent_run_status(run_id: str, db: Session = Depends(get_db)) -> dict:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run_to_task_status(run)


@router.get("/sessions", response_model=list[SessionSummary])
def list_sessions(course_id: str | None = None, db: Session = Depends(get_db)) -> list[QASession]:
    course = get_requested_course(db, course_id)
    return list(
        db.scalars(
            select(QASession).where(QASession.course_id == course.id).order_by(QASession.updated_at.desc())
        ).all()
    )


@router.get("/sessions/{session_id}", response_model=SessionSummary)
def get_session(session_id: str, db: Session = Depends(get_db)) -> QASession:
    session = db.get(QASession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.get("/sessions/{session_id}/messages", response_model=SessionMessagesResponse)
def get_session_messages(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = db.get(QASession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session.id, "messages": session.transcript or []}


@router.delete("/sessions/{session_id}", response_model=DeleteResponse)
def delete_session(session_id: str, db: Session = Depends(get_db)) -> dict:
    session = db.get(QASession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    run_ids = [run.id for run in db.scalars(select(AgentRun).where(AgentRun.session_id == session_id)).all()]
    if run_ids:
        db.query(AgentTraceEvent).filter(AgentTraceEvent.run_id.in_(run_ids)).delete(synchronize_session=False)
        db.query(AgentRun).filter(AgentRun.id.in_(run_ids)).delete(synchronize_session=False)
    db.delete(session)
    db.commit()
    return {"deleted": True}
