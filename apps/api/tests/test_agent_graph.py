from __future__ import annotations

import pytest

from app.schemas import AgentRequest
from app.services.embeddings import ChatCallResult


@pytest.mark.asyncio
async def test_agent_clarify_route_does_not_use_fallback(db_session, sample_course):
    from app.core.config import get_settings
    from app.services.agent_graph import run_agent

    assert get_settings().enable_model_fallback is False
    response = await run_agent(db_session, AgentRequest(question="it", course_id=sample_course.id))

    assert response["route"] == "clarify"
    assert response["citations"] == []
    assert response["degraded_mode"] is False
    assert response["answer_model_audit"]["external_called"] is False
    assert response["answer_model_audit"]["skipped_reason"] == "clarify_route"


@pytest.mark.asyncio
async def test_agent_retrieval_qa_path_uses_real_provider_metadata(db_session, sample_course, indexed_chunks, monkeypatch):
    import app.services.agent_graph as agent_graph
    from app.services.embeddings import ChatProvider

    _, chunks = indexed_chunks
    search_payload = {
        "chunk_id": chunks[0].id,
        "snippet": chunks[0].snippet,
        "score": 1.0,
        "citations": [
            {
                "chunk_id": chunks[0].id,
                "document_id": chunks[0].document_id,
                "document_title": "Centrality Notes",
                "source_path": "centrality.md",
                "chapter": "L3",
                "section": "Centrality",
                "page_number": None,
                "snippet": chunks[0].snippet,
            }
        ],
        "metadata": {"scores": {"dense": 1.0}},
        "content": chunks[0].content,
        "document_title": "Centrality Notes",
        "source_path": "centrality.md",
        "chapter": "L3",
        "source_type": "markdown",
    }

    async def fake_rewrite(self, question, history=None):
        return question

    async def fake_hybrid_search(db, course_id, query, filters, top_k):
        return [search_payload]

    async def fake_answer(self, question, contexts, history=None):
        return ChatCallResult(
            answer="Degree centrality counts incident edges.",
            provider="openai_compatible_chat",
            model="unit-test-chat",
            external_called=True,
            fallback_reason=None,
        )

    monkeypatch.setattr(ChatProvider, "rewrite_question", fake_rewrite)
    monkeypatch.setattr(ChatProvider, "answer_question_with_meta", fake_answer)
    monkeypatch.setattr(agent_graph, "hybrid_search_chunks", fake_hybrid_search)

    response = await agent_graph.run_agent(
        db_session,
        AgentRequest(question="define degree centrality concept", course_id=sample_course.id, top_k=3),
    )

    assert response["route"] == "retrieve_notes"
    assert response["answer"]
    assert response["citations"][0]["chunk_id"] == chunks[0].id
    assert response["answer_model_audit"]["provider"] == "openai_compatible_chat"
    assert response["answer_model_audit"]["external_called"] is True
    answer_trace = next(item for item in response["trace"] if item["node"] == "answer_generator")
    assert "provider=openai_compatible_chat" in answer_trace["output_summary"]
    assert "fallback=None" in answer_trace["output_summary"]


@pytest.mark.asyncio
async def test_related_question_in_this_course_routes_to_multi_hop(db_session, sample_course, indexed_chunks, monkeypatch):
    import app.services.agent_graph as agent_graph
    from app.services.embeddings import ChatProvider

    _, chunks = indexed_chunks
    search_payload = {
        "chunk_id": chunks[0].id,
        "snippet": chunks[0].snippet,
        "score": 1.0,
        "citations": [
            {
                "chunk_id": chunks[0].id,
                "document_id": chunks[0].document_id,
                "document_title": "Centrality Notes",
                "source_path": "centrality.md",
                "chapter": "L3",
                "section": "Centrality",
                "page_number": None,
                "snippet": chunks[0].snippet,
            }
        ],
        "metadata": {"scores": {"dense": 1.0}},
        "content": chunks[0].content,
        "document_title": "Centrality Notes",
        "source_path": "centrality.md",
        "chapter": "L3",
        "source_type": "markdown",
    }

    async def fake_rewrite(self, question, history=None):
        return question

    async def fake_graph_search(db, course_id, query, filters, top_k):
        return [search_payload]

    async def fake_answer(self, question, contexts, history=None):
        return ChatCallResult(
            answer="The two concepts are related in the course notes.",
            provider="openai_compatible_chat",
            model="unit-test-chat",
            external_called=True,
            fallback_reason=None,
        )

    monkeypatch.setattr(ChatProvider, "rewrite_question", fake_rewrite)
    monkeypatch.setattr(ChatProvider, "answer_question_with_meta", fake_answer)
    monkeypatch.setattr(agent_graph, "graph_enhanced_search", fake_graph_search)

    response = await agent_graph.run_agent(
        db_session,
        AgentRequest(question="Explain how degree centrality is related to closeness centrality in this course.", course_id=sample_course.id, top_k=3),
    )

    assert response["route"] == "multi_hop_research"
    assert response["answer_model_audit"]["external_called"] is True
    assert response["citations"][0]["chunk_id"] == chunks[0].id


@pytest.mark.asyncio
async def test_stream_agent_events_emits_trace_before_answer_tokens(db_session, sample_course, monkeypatch):
    import asyncio
    import app.services.agent_graph as agent_graph

    class FakeGraph:
        async def ainvoke(self, initial):
            agent_graph._trace(
                initial["db"],
                initial["run_id"],
                "router",
                output_summary="route=retrieve_notes",
            )
            await asyncio.sleep(0)
            agent_graph._trace(
                initial["db"],
                initial["run_id"],
                "retrievers",
                output_summary="1 candidate chunks",
            )
            return {
                "answer": "streamed answer",
                "citations": [],
                "graded_documents": [],
                "route": "retrieve_notes",
            }

    monkeypatch.setattr(agent_graph, "AGENT_GRAPH", FakeGraph())

    events = []
    async for event in agent_graph.stream_agent_events(
        db_session,
        AgentRequest(question="define centrality", course_id=sample_course.id, top_k=3, stream_trace=True),
    ):
        events.append(event)

    event_types = [event["type"] for event in events]
    assert event_types.index("trace") < event_types.index("token")
    assert [event["trace"]["node"] for event in events if event["type"] == "trace"][:2] == ["router", "retrievers"]
