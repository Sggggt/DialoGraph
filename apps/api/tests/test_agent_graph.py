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

    async def fake_perceive(self, question, history=None):
        return {
            "intent": "definition",
            "entities": ["degree centrality"],
            "sub_queries": [question],
            "needs_graph": False,
            "suggested_strategy": "base_retrieval",
        }

    async def fake_hybrid_search(db, course_id, query, filters, top_k):
        return [search_payload]

    async def fake_answer(self, question, contexts, history=None, evidence_quality="normal"):
        return ChatCallResult(
            answer="Degree centrality counts incident edges.",
            provider="openai_compatible_chat",
            model="unit-test-chat",
            external_called=True,
            fallback_reason=None,
        )

    monkeypatch.setattr(ChatProvider, "rewrite_question", fake_rewrite)
    monkeypatch.setattr(ChatProvider, "perceive_question", fake_perceive)
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

    async def fake_perceive(self, question, history=None):
        return {
            "intent": "comparison",
            "entities": ["degree centrality", "closeness centrality"],
            "sub_queries": [question],
            "needs_graph": True,
            "suggested_strategy": "evidence_chain",
        }

    async def fake_hybrid_search(db, course_id, query, filters, top_k):
        return [search_payload]

    async def fake_answer(self, question, contexts, history=None, evidence_quality="normal"):
        return ChatCallResult(
            answer="The two concepts are related in the course notes.",
            provider="openai_compatible_chat",
            model="unit-test-chat",
            external_called=True,
            fallback_reason=None,
        )

    monkeypatch.setattr(ChatProvider, "rewrite_question", fake_rewrite)
    monkeypatch.setattr(ChatProvider, "perceive_question", fake_perceive)
    monkeypatch.setattr(ChatProvider, "answer_question_with_meta", fake_answer)
    monkeypatch.setattr(agent_graph, "hybrid_search_chunks", fake_hybrid_search)

    response = await agent_graph.run_agent(
        db_session,
        AgentRequest(question="Explain how degree centrality is related to closeness centrality in this course.", course_id=sample_course.id, top_k=3),
    )

    assert response["route"] == "multi_hop_research"
    assert response["answer_model_audit"]["external_called"] is True
    assert response["citations"][0]["chunk_id"] == chunks[0].id


@pytest.mark.asyncio
async def test_retrieval_decision_skips_retrieval_for_pronoun_with_history(db_session, sample_course, monkeypatch):
    from app.services.agent_graph import RetrievalDecision

    decision = RetrievalDecision()
    state1 = {
        "run_id": "run-1",
        "question": "Explain it again",
        "history": [{"role": "user", "content": "What is degree centrality?"}, {"role": "assistant", "content": "It counts edges."}],
    }
    result = await decision(state1)
    assert result["skip_retrieval"] is True

    state2 = {
        "run_id": "run-2",
        "question": "What is closeness centrality?",
        "history": [{"role": "user", "content": "What is degree centrality?"}, {"role": "assistant", "content": "It counts edges."}],
    }
    result = await decision(state2)
    assert result["skip_retrieval"] is False


@pytest.mark.asyncio
async def test_route_after_reflection_respects_max_retries(db_session, sample_course, monkeypatch):
    from app.core.config import get_settings
    from app.services.agent_graph import route_after_reflection

    monkeypatch.setenv("ENABLE_AGENTIC_REFLECTION", "true")
    monkeypatch.setenv("ENABLE_POST_GENERATION_REFLECTION", "true")
    monkeypatch.setenv("REFLECTION_MAX_RETRIES", "2")
    get_settings.cache_clear()

    state = {"reflection_result": {"has_issue": True}, "retry_count": 0}
    assert route_after_reflection(state) == "answer_corrector"

    state["retry_count"] = 1
    assert route_after_reflection(state) == "answer_corrector"

    state["retry_count"] = 2
    assert route_after_reflection(state) == "self_check"

    state["reflection_result"] = {"has_issue": False}
    assert route_after_reflection(state) == "self_check"


def test_route_after_corrector_routes_by_issue_type():
    from app.services.agent_graph import route_after_corrector

    state = {"reflection_result": {"issue_type": "hallucination"}}
    assert route_after_corrector(state) == "context_synthesizer"

    state = {"reflection_result": {"issue_type": "insufficient_coverage"}}
    assert route_after_corrector(state) == "base_retrieval"

    state = {"reflection_result": {"issue_type": "contradiction"}}
    assert route_after_corrector(state) == "base_retrieval"


@pytest.mark.asyncio
async def test_answer_corrector_updates_params_by_issue_type(db_session, sample_course):
    from app.services.agent_graph import AnswerCorrector

    corrector = AnswerCorrector()

    state = {
        "run_id": "run-1",
        "question": "q",
        "rewritten_question": "q",
        "top_k": 3,
        "graded_documents": [
            {"metadata": {"scores": {"grade_score": 0.2}}},
            {"metadata": {"scores": {"grade_score": 0.5}}},
        ],
        "reflection_result": {"issue_type": "insufficient_coverage"},
        "retry_count": 0,
    }
    result = await corrector(state)
    assert result["top_k"] == 6
    assert result["retry_count"] == 1

    state["reflection_result"] = {"issue_type": "hallucination"}
    result = await corrector(state)
    assert result["retry_count"] == 1
    # hallucination: keep all graded docs (one has grade_score 0.5, not all < 0.2)
    assert len(result["graded_documents"]) == 2

    state["reflection_result"] = {"issue_type": "contradiction"}
    result = await corrector(state)
    assert "consistent explanation" in result["rewritten_question"]
    assert result["sub_queries"] == [result["rewritten_question"]]


@pytest.mark.asyncio
async def test_stream_agent_events_emits_trace_before_answer_tokens(db_session, sample_course, monkeypatch):
    import asyncio
    import app.services.agent_graph as agent_graph

    class FakeGraph:
        async def ainvoke(self, initial):
            await agent_graph._trace(
                initial["run_id"],
                "router",
                output_summary="route=retrieve_notes",
            )
            await asyncio.sleep(0)
            await agent_graph._trace(
                initial["run_id"],
                "base_retrieval",
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
    assert [event["trace"]["node"] for event in events if event["type"] == "trace"][:2] == ["router", "base_retrieval"]


@pytest.mark.asyncio
async def test_evidence_evaluator_marks_sufficient_for_high_quality_docs(db_session, sample_course):
    from app.services.agent_graph import EvidenceEvaluator

    evaluator = EvidenceEvaluator()
    state = {
        "run_id": "run-ev-1",
        "question": "q",
        "top_k": 3,
        "retry_count": 0,
        "perception_result": {"intent": "definition"},
        "graded_documents": [
            {"chunk_id": "c1", "metadata": {"scores": {"grade_score": 0.5}}},
        ],
    }
    result = await evaluator(state)
    assert result["evidence_evaluation"]["sufficient"] is True
    assert result["evidence_evaluation"]["reason"] == "sufficient"
    assert "low_evidence" not in result
    assert "retry_count" not in result


@pytest.mark.asyncio
async def test_evidence_evaluator_triggers_retry_when_insufficient(db_session, sample_course):
    from app.services.agent_graph import EvidenceEvaluator

    evaluator = EvidenceEvaluator()
    state = {
        "run_id": "run-ev-2",
        "question": "q",
        "top_k": 3,
        "retry_count": 0,
        "perception_result": {"intent": "comparison"},
        "graded_documents": [
            {"chunk_id": "c1", "metadata": {"scores": {"grade_score": 0.1}}},
        ],
    }
    result = await evaluator(state)
    assert result["evidence_evaluation"]["sufficient"] is False
    assert result["retry_count"] == 1
    assert result["top_k"] == 6
    assert "low_evidence" not in result


@pytest.mark.asyncio
async def test_evidence_evaluator_sets_low_evidence_after_max_retries(db_session, sample_course):
    from app.services.agent_graph import EvidenceEvaluator

    evaluator = EvidenceEvaluator()
    state = {
        "run_id": "run-ev-3",
        "question": "q",
        "top_k": 3,
        "retry_count": 2,
        "perception_result": {"intent": "analysis"},
        "graded_documents": [],
    }
    result = await evaluator(state)
    assert result["evidence_evaluation"]["sufficient"] is False
    assert result["low_evidence"] is True
    assert "retry_count" not in result


@pytest.mark.asyncio
async def test_evidence_evaluator_marginal_with_anchor(db_session, sample_course):
    from app.services.agent_graph import EvidenceEvaluator

    evaluator = EvidenceEvaluator()
    state = {
        "run_id": "run-ev-4",
        "question": "q",
        "top_k": 3,
        "retry_count": 0,
        "perception_result": {"intent": "definition"},
        "graded_documents": [
            {"chunk_id": "c1", "metadata": {"scores": {"grade_score": 0.4}}},
        ],
    }
    result = await evaluator(state)
    # Anchor present (0.4 >= 0.35) so marginal/sufficient even if only 1 doc
    assert result["evidence_evaluation"]["sufficient"] is True


def test_route_after_evidence_evaluator():
    from app.services.agent_graph import route_after_evidence_evaluator

    # Sufficient -> context_synthesizer
    assert route_after_evidence_evaluator({"evidence_evaluation": {"sufficient": True}}) == "context_synthesizer"

    # Insufficient with retry budget -> retrieval_planner
    assert route_after_evidence_evaluator({"evidence_evaluation": {"sufficient": False}, "retry_count": 0}) == "retrieval_planner"
    assert route_after_evidence_evaluator({"evidence_evaluation": {"sufficient": False}, "retry_count": 1}) == "retrieval_planner"

    # Insufficient without retry budget -> context_synthesizer (with low_evidence flag already set by evaluator)
    assert route_after_evidence_evaluator({"evidence_evaluation": {"sufficient": False}, "retry_count": 2}) == "context_synthesizer"
    assert route_after_evidence_evaluator({"evidence_evaluation": {"sufficient": False}, "retry_count": 5}) == "context_synthesizer"
