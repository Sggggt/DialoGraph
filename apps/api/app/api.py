from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AgentRun, QASession
from app.schemas import (
    AgentRequest,
    AgentResponse,
    BatchStartResponse,
    ConceptCard,
    CourseCreateRequest,
    CourseSummary,
    DashboardSnapshot,
    GraphNodeDetail,
    GraphResponse,
    IngestionBatchSummary,
    JobStatusResponse,
    QARequest,
    QAResponse,
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
    create_job,
    create_sync_batch,
    get_batch_status,
    list_course_summaries,
    resolve_course,
    run_batch_ingestion,
    run_ingestion_job,
    summarize_course,
)
from app.services.retrieval import get_dashboard_snapshot, get_job_status, search_chunks
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


@router.post("/files/upload", response_model=UploadFileResponse)
async def upload_file(
    background_tasks: BackgroundTasks,
    course_id: str | None = None,
    upload: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> dict:
    course = get_requested_course(db, course_id)
    stored_path = await save_upload(upload, course.name)
    job = create_job(db, course.id, None, "upload", source_path=str(stored_path))
    background_tasks.add_task(enqueue_ingestion, job.id, str(stored_path), "upload")
    return {"document_id": "", "job_id": job.id, "status": "queued"}


@router.post("/ingestion/sync-source", response_model=BatchStartResponse)
async def sync_source_directory(background_tasks: BackgroundTasks, course_id: str | None = None, db: Session = Depends(get_db)) -> dict:
    course = get_requested_course(db, course_id)
    root = Path(course.source_root)
    if not root.exists():
        raise HTTPException(status_code=404, detail=f"Source root not found: {root}")
    batch = create_sync_batch(db, course.id, root, trigger_source="sync")
    background_tasks.add_task(enqueue_batch, batch.id)
    return {"batch_id": batch.id, "state": "queued"}


@router.get("/ingestion/batches/{batch_id}", response_model=IngestionBatchSummary)
def batch_status(batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = get_batch_status(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


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
