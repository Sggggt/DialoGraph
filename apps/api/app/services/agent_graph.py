from __future__ import annotations

import re
import time
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentRun, AgentTraceEvent, QASession
from app.schemas import AgentRequest, SearchFilters
from app.services.embeddings import ChatProvider, is_degraded_mode
from app.services.ingestion import resolve_course
from app.services.retrieval import hybrid_search_chunks


AgentRoute = Literal["direct_answer", "retrieve_notes", "retrieve_exercises", "retrieve_both", "clarify", "multi_hop_research"]
NOTE_SOURCE_TYPES = {"pdf", "ppt", "pptx", "docx", "markdown", "text", "html", "image"}
EXERCISE_MARKERS = ("exercise", "homework", "problem", "assignment", "quiz", "exam")


class AgentState(TypedDict, total=False):
    db: Session
    run_id: str
    session_id: str
    course_id: str
    question: str
    rewritten_question: str
    history: list[dict]
    filters: SearchFilters
    top_k: int
    route: AgentRoute
    sub_queries: list[str]
    documents: list[dict]
    graded_documents: list[dict]
    context: str
    answer: str
    citations: list[dict]
    retry_count: int
    degraded_mode: bool


class QueryAnalysis(BaseModel):
    normalized_question: str
    token_count: int
    likely_course_query: bool
    needs_clarification: bool
    is_multi_hop: bool


def _terms(text: str) -> set[str]:
    return {term for term in re.findall(r"[a-zA-Z0-9_]+", text.lower()) if len(term) > 2}


def _summarize(text: str, limit: int = 280) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:limit]


def _set_run_state(
    db: Session,
    run_id: str,
    state: str,
    *,
    current_node: str | None = None,
    route: str | None = None,
    retry_count: int | None = None,
    answer: str | None = None,
    error: str | None = None,
) -> None:
    run = db.get(AgentRun, run_id)
    if run is None:
        return
    run.status = state
    run.current_node = current_node
    if state == "running" and run.started_at is None:
        run.started_at = datetime.utcnow()
    if state in {"completed", "failed", "needs_clarification"}:
        run.completed_at = datetime.utcnow()
    if route is not None:
        run.route = route
    if retry_count is not None:
        run.retry_count = retry_count
    if answer is not None:
        run.final_answer = answer
    if error is not None:
        run.error_message = error
    db.commit()


def _trace(
    db: Session,
    run_id: str,
    node: str,
    *,
    input_summary: str | None = None,
    output_summary: str | None = None,
    document_ids: list[str] | None = None,
    scores: dict | None = None,
    duration_ms: int = 0,
    status: str = "completed",
    error: str | None = None,
) -> dict:
    event = AgentTraceEvent(
        run_id=run_id,
        node=node,
        status=status,
        input_summary=input_summary,
        output_summary=output_summary,
        document_ids=document_ids or [],
        scores=scores or {},
        duration_ms=duration_ms,
        error_message=error,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return trace_event_to_payload(event)


def trace_event_to_payload(event: AgentTraceEvent) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "node": event.node,
        "status": event.status,
        "input_summary": event.input_summary,
        "output_summary": event.output_summary,
        "document_ids": event.document_ids or [],
        "scores": event.scores or {},
        "duration_ms": event.duration_ms,
        "error": event.error_message,
        "created_at": event.created_at,
    }


class QueryAnalyzer:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        question = state["question"].strip()
        tokens = _terms(question)
        lower = question.lower()
        analysis = QueryAnalysis(
            normalized_question=question,
            token_count=len(tokens),
            likely_course_query=not any(term in lower for term in ("weather", "stock price", "joke", "movie ticket")),
            needs_clarification=len(tokens) < 2 or lower in {"it", "this", "that", "explain it", "why"},
            is_multi_hop=any(term in lower for term in ("compare", "relationship", "difference between", "connect", "derive", "prove")),
        )
        _set_run_state(db, state["run_id"], "running", current_node="query_analyzer")
        _trace(
            db,
            state["run_id"],
            "query_analyzer",
            input_summary=question,
            output_summary=analysis.model_dump_json(),
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"rewritten_question": analysis.normalized_question, "sub_queries": [], "degraded_mode": is_degraded_mode()}


class Router:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        question = state["question"]
        lower = question.lower()
        terms = _terms(question)
        if any(greeting in lower for greeting in ("hello", "hi", "who are you", "help", "what can you do")):
            route: AgentRoute = "direct_answer"
        elif len(terms) < 2 or lower in {"it", "this", "that", "explain it", "why"}:
            route = "clarify"
        elif any(marker in lower for marker in EXERCISE_MARKERS):
            route = "retrieve_exercises"
        elif any(term in lower for term in ("compare", "relationship", "difference between", "connect", "derive", "prove")):
            route = "multi_hop_research"
        elif any(term in lower for term in ("note", "slide", "definition", "concept", "chapter")):
            route = "retrieve_notes"
        else:
            route = "retrieve_both"
        _set_run_state(db, state["run_id"], "running", current_node="router", route=route)
        _trace(
            db,
            state["run_id"],
            "router",
            input_summary=question,
            output_summary=f"route={route}",
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"route": route}


class QueryRewriter:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        chat = ChatProvider()
        rewritten = await chat.rewrite_question(state["question"], state.get("history", []))
        if state.get("route") == "multi_hop_research":
            sub_queries = split_multi_hop_query(rewritten)
        else:
            sub_queries = [rewritten]
        _set_run_state(db, state["run_id"], "running", current_node="query_rewriter")
        _trace(
            db,
            state["run_id"],
            "query_rewriter",
            input_summary=state["question"],
            output_summary=" | ".join(sub_queries),
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"rewritten_question": rewritten, "sub_queries": sub_queries}


class HybridRetriever:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        queries = state.get("sub_queries") or [state.get("rewritten_question") or state["question"]]
        all_results: dict[str, dict] = {}
        for query in queries:
            for filters in expand_route_filters(state["route"], state["filters"]):
                results = await hybrid_search_chunks(db, state["course_id"], query, filters, max(state["top_k"] * 2, state["top_k"]))
                for result in results:
                    current = all_results.get(result["chunk_id"])
                    if current is None or result["score"] > current["score"]:
                        all_results[result["chunk_id"]] = result
        documents = sorted(all_results.values(), key=lambda item: item["score"], reverse=True)[: max(state["top_k"] * 2, state["top_k"])]
        _set_run_state(db, state["run_id"], "running", current_node="retrievers")
        _trace(
            db,
            state["run_id"],
            "retrievers",
            input_summary=" | ".join(queries),
            output_summary=f"{len(documents)} candidate chunks",
            document_ids=[item["chunk_id"] for item in documents],
            scores={item["chunk_id"]: item.get("metadata", {}).get("scores", {}) for item in documents},
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"documents": documents}


class DocumentGrader:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        query_terms = _terms(state.get("rewritten_question") or state["question"])
        graded = []
        for document in state.get("documents", []):
            haystack = f"{document.get('document_title', '')} {document.get('snippet', '')} {document.get('content', '')}"
            overlap = len(query_terms.intersection(_terms(haystack)))
            scores = document.setdefault("metadata", {}).setdefault("scores", {})
            scores["grader_overlap"] = overlap
            if overlap > 0 or document["score"] > 0.01:
                graded.append(document)
        retry_count = state.get("retry_count", 0)
        _set_run_state(db, state["run_id"], "running", current_node="document_grader", retry_count=retry_count)
        _trace(
            db,
            state["run_id"],
            "document_grader",
            input_summary=f"{len(state.get('documents', []))} candidates",
            output_summary=f"{len(graded)} accepted",
            document_ids=[item["chunk_id"] for item in graded],
            scores={item["chunk_id"]: item.get("metadata", {}).get("scores", {}) for item in graded},
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"graded_documents": graded[: state["top_k"]], "retry_count": retry_count}


class RetryPlanner:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        retry_count = state.get("retry_count", 0) + 1
        next_question = f"{state.get('rewritten_question') or state['question']} course lecture notes examples"
        _set_run_state(db, state["run_id"], "running", current_node="retry_planner", retry_count=retry_count)
        _trace(
            db,
            state["run_id"],
            "retry_planner",
            input_summary=f"retry={retry_count}",
            output_summary=next_question,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"retry_count": retry_count, "rewritten_question": next_question, "sub_queries": [next_question]}


class ContextSynthesizer:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        context = "\n\n".join(
            f"[{idx + 1}] {item['document_title']} / {item.get('chapter') or 'General'}\n{item['content'][:1800]}"
            for idx, item in enumerate(state.get("graded_documents", []))
        )
        _set_run_state(db, state["run_id"], "running", current_node="context_synthesizer")
        _trace(
            db,
            state["run_id"],
            "context_synthesizer",
            input_summary=f"{len(state.get('graded_documents', []))} graded chunks",
            output_summary=_summarize(context),
            document_ids=[item["chunk_id"] for item in state.get("graded_documents", [])],
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"context": context}


class AnswerGenerator:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        route = state["route"]
        if route == "direct_answer":
            answer = "I can answer questions about the indexed course materials, show citations, and explain how the retrieval agent reached its answer."
            citations: list[dict] = []
            used_chunks: list[dict] = []
        elif route == "clarify":
            answer = "Please clarify the course concept, chapter, exercise, or comparison you want me to retrieve."
            citations = []
            used_chunks = []
        else:
            used_chunks = state.get("graded_documents", [])
            answer = await ChatProvider().answer_question(state["question"], used_chunks, state.get("history", []))
            citations = [citation for item in used_chunks for citation in item["citations"]]
        _set_run_state(db, state["run_id"], "running", current_node="answer_generator")
        _trace(
            db,
            state["run_id"],
            "answer_generator",
            input_summary=state["question"],
            output_summary=_summarize(answer),
            document_ids=[item["chunk_id"] for item in state.get("graded_documents", [])],
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"answer": answer, "citations": citations, "graded_documents": used_chunks}


class CitationChecker:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        allowed = {item["chunk_id"] for item in state.get("graded_documents", [])}
        citations = [citation for citation in state.get("citations", []) if citation.get("chunk_id") in allowed]
        if state["route"] in {"direct_answer", "clarify"}:
            citations = []
        _set_run_state(db, state["run_id"], "running", current_node="citation_checker")
        _trace(
            db,
            state["run_id"],
            "citation_checker",
            input_summary=f"{len(state.get('citations', []))} citations",
            output_summary=f"{len(citations)} verified citations",
            document_ids=[item["chunk_id"] for item in citations],
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {"citations": citations}


class SelfCheck:
    async def __call__(self, state: AgentState) -> dict:
        start = time.perf_counter()
        db = state["db"]
        if state["route"] == "clarify":
            status = "needs_clarification"
        else:
            status = "completed"
        _set_run_state(db, state["run_id"], status, current_node=None, answer=state.get("answer"))
        _trace(
            db,
            state["run_id"],
            "self_check",
            input_summary=state["route"],
            output_summary=status,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        return {}


class Clarify:
    async def __call__(self, state: AgentState) -> dict:
        return await AnswerGenerator().__call__(state)


def split_multi_hop_query(question: str) -> list[str]:
    parts = [part.strip(" ,.;") for part in re.split(r"\band\b|;|,", question, flags=re.IGNORECASE) if part.strip(" ,.;")]
    if len(parts) >= 2:
        return parts[:3]
    return [question, f"background for {question}", f"relationships in {question}"]


def expand_route_filters(route: AgentRoute, filters: SearchFilters) -> list[SearchFilters]:
    if filters.source_type:
        return [filters]
    if route == "retrieve_exercises":
        next_filters = filters.model_copy()
        next_filters.source_type = "notebook"
        return [next_filters]
    if route == "retrieve_notes":
        return [filters.model_copy(update={"source_type": source_type}) for source_type in sorted(NOTE_SOURCE_TYPES)]
    return [filters]


def route_after_router(state: AgentState) -> str:
    route = state["route"]
    if route in {"direct_answer", "clarify"}:
        return "answer_generator"
    return "query_rewriter"


def route_after_grader(state: AgentState) -> str:
    if state.get("graded_documents"):
        return "context_synthesizer"
    if state.get("retry_count", 0) < 2:
        return "retry_planner"
    return "context_synthesizer"


def build_agent_graph():
    workflow = StateGraph(AgentState)
    workflow.add_node("query_analyzer", QueryAnalyzer())
    workflow.add_node("router", Router())
    workflow.add_node("query_rewriter", QueryRewriter())
    workflow.add_node("retrievers", HybridRetriever())
    workflow.add_node("document_grader", DocumentGrader())
    workflow.add_node("retry_planner", RetryPlanner())
    workflow.add_node("context_synthesizer", ContextSynthesizer())
    workflow.add_node("answer_generator", AnswerGenerator())
    workflow.add_node("citation_checker", CitationChecker())
    workflow.add_node("self_check", SelfCheck())
    workflow.add_edge(START, "query_analyzer")
    workflow.add_edge("query_analyzer", "router")
    workflow.add_conditional_edges("router", route_after_router, {"answer_generator": "answer_generator", "query_rewriter": "query_rewriter"})
    workflow.add_edge("query_rewriter", "retrievers")
    workflow.add_edge("retrievers", "document_grader")
    workflow.add_conditional_edges(
        "document_grader",
        route_after_grader,
        {"retry_planner": "retry_planner", "context_synthesizer": "context_synthesizer"},
    )
    workflow.add_edge("retry_planner", "retrievers")
    workflow.add_edge("context_synthesizer", "answer_generator")
    workflow.add_edge("answer_generator", "citation_checker")
    workflow.add_edge("citation_checker", "self_check")
    workflow.add_edge("self_check", END)
    return workflow.compile()


AGENT_GRAPH = build_agent_graph()


def create_or_get_session(db: Session, course_id: str, session_id: str | None, question: str) -> QASession:
    session = db.get(QASession, session_id) if session_id else None
    if session is not None and session.course_id != course_id:
        session = None
    if session is None:
        session = QASession(course_id=course_id, title=_summarize(question, 80), transcript=[])
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def append_session_turn(db: Session, session_id: str, question: str, answer: str, run_id: str, citations: list[dict]) -> None:
    session = db.get(QASession, session_id)
    if session is None:
        return
    transcript = list(session.transcript or [])
    transcript.append({"role": "user", "content": question, "run_id": run_id})
    transcript.append({"role": "assistant", "content": answer, "run_id": run_id, "citations": citations})
    session.transcript = transcript
    session.last_question = question
    session.last_answer = answer
    session.updated_at = datetime.utcnow()
    db.commit()


def run_to_task_status(run: AgentRun) -> dict:
    return {
        "run_id": run.id,
        "state": run.status,
        "current_node": run.current_node,
        "retry_count": run.retry_count,
        "route": run.route,
        "error": run.error_message,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


async def run_agent(db: Session, request: AgentRequest) -> dict:
    course = resolve_course(db, request.course_id)
    session = create_or_get_session(db, course.id, request.session_id, request.question)
    run = AgentRun(
        course_id=course.id,
        session_id=session.id,
        question=request.question,
        status="queued",
        metadata_json={"top_k": request.top_k, "filters": request.filters.model_dump()},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    history = [item.model_dump() for item in request.history] or list(session.transcript or [])[-8:]
    initial: AgentState = {
        "db": db,
        "run_id": run.id,
        "session_id": session.id,
        "course_id": course.id,
        "question": request.question,
        "history": history,
        "filters": request.filters,
        "top_k": request.top_k,
        "retry_count": 0,
    }
    try:
        final_state = await AGENT_GRAPH.ainvoke(initial)
        trace_events = db.scalars(select(AgentTraceEvent).where(AgentTraceEvent.run_id == run.id).order_by(AgentTraceEvent.created_at.asc())).all()
        used_chunks = final_state.get("graded_documents", [])
        answer = final_state.get("answer", "")
        citations = final_state.get("citations", [])
        append_session_turn(db, session.id, request.question, answer, run.id, citations)
        return {
            "run_id": run.id,
            "session_id": session.id,
            "answer": answer,
            "citations": citations,
            "used_chunks": used_chunks,
            "route": final_state.get("route") or "retrieve_both",
            "trace": [trace_event_to_payload(event) for event in trace_events],
            "degraded_mode": is_degraded_mode(),
        }
    except Exception as exc:
        _set_run_state(db, run.id, "failed", current_node=None, error=str(exc))
        _trace(db, run.id, "error", status="failed", output_summary=str(exc), error=str(exc))
        raise


async def stream_agent_events(db: Session, request: AgentRequest) -> AsyncGenerator[dict, None]:
    response = await run_agent(db, request)
    if request.stream_trace:
        for event in response["trace"]:
            yield {"type": "trace", "trace": event}
    for line in response["answer"].splitlines() or [response["answer"]]:
        yield {"type": "token", "token": line}
    yield {"type": "citations", "citations": response["citations"], "degraded_mode": response["degraded_mode"]}
    yield {"type": "final", "response": response}
