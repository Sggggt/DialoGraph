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
    answer_trace = next(item for item in response["trace"] if item["node"] == "answer_generator")
    assert "provider=openai_compatible_chat" in answer_trace["output_summary"]
    assert "fallback=None" in answer_trace["output_summary"]
