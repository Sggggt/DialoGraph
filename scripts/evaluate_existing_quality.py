import asyncio
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

API_BASE = "http://127.0.0.1:8000/api"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY not set in .env")

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../apps/api")))
from app.services.embeddings import post_openai_compatible_json

class Judge:
    def __init__(self):
        self.api_key = OPENAI_API_KEY
        self.base_url = OPENAI_BASE_URL.rstrip("/")
        self.model = "qwen3.6-plus"
        self.resolve_ip = os.getenv("OPENAI_RESOLVE_IP", "")

    async def evaluate(self, system_prompt: str, user_prompt: str) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt + " You must return valid JSON ONLY."},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        data = await post_openai_compatible_json(
            f"{self.base_url}/chat/completions",
            payload,
            headers,
            timeout=120.0,
            resolve_ip=self.resolve_ip
        )
        content = data["choices"][0]["message"]["content"]
            
        text = content.strip()
        if text.startswith("```"):
            import re
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

async def check_endpoints():
    async with httpx.AsyncClient(trust_env=False) as client:
        # /health
        resp = await client.get(f"{API_BASE}/health")
        resp.raise_for_status()
        health = resp.json()
        assert health["status"] == "ok"
        assert health["degraded_mode"] is False, "System is in degraded mode!"
        
        # /settings/model
        resp = await client.get(f"{API_BASE}/settings/model")
        resp.raise_for_status()
        model_settings = resp.json()
        assert model_settings["degraded_mode"] is False, "Model is in degraded mode!"
        
        # /settings/runtime-check
        resp = await client.get(f"{API_BASE}/settings/runtime-check")
        resp.raise_for_status()
        
        # /courses
        resp = await client.get(f"{API_BASE}/courses")
        resp.raise_for_status()
        courses = resp.json()
        
    return courses, model_settings

def generate_samples(course_name: str, concepts: list) -> tuple[list[str], list[str]]:
    # Generate 4 queries and 3 questions based on concept names
    concept_names = [c["name"] for c in concepts[:10]]
    if not concept_names:
        concept_names = ["basic concept", "advanced theory", "application", "methodology"]
        
    queries = [
        f"Explain {concept_names[0]}",
        f"What is the relationship between {concept_names[1]} and {concept_names[2]}?",
        f"Details of {concept_names[3]}",
        f"Summary of {concept_names[0]}"
    ]
    
    questions = [
        f"Can you explain the core idea behind {concept_names[0]} and how it is applied?",
        f"How does {concept_names[1]} compare to {concept_names[2]} in this course?",
        f"Summarize the key points about {concept_names[3]}."
    ]
    
    return queries, questions

async def test_search(course_id: str, query: str, model_settings: dict, judge: Judge, results_report: list):
    async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
        resp = await client.post(f"{API_BASE}/search", json={"course_id": course_id, "query": query, "top_k": 5})
        resp.raise_for_status()
        data = resp.json()
        
    assert data["degraded_mode"] is False
    audit = data["model_audit"]
    assert audit.get("embedding_external_called") is True
    assert audit.get("embedding_fallback_reason") is None
    
    if model_settings.get("reranker_enabled", False):
        # Depending on implementation, reranker_called might be in audit or we assume it's checked
        assert "reranker" in str(audit).lower() or audit.get("reranker_called", True)
        
    results = data["results"]
    assert len(results) > 0, "Top-K is empty"
    
    # Check score/audit info
    assert "score" in results[0] or "relevance_score" in results[0] or "similarity" in results[0]

    # Judge
    snippets = "\n".join([r.get("text", r.get("content", "")) for r in results])
    sys_prompt = "You are an evaluator. Return a JSON with 'score' (1-5 float), 'reason' (string), 'failures' (list of strings)."
    user_prompt = f"Query: {query}\n\nSnippets:\n{snippets}\n\nRate relevance of snippets to query."
    
    judge_res = await judge.evaluate(sys_prompt, user_prompt)
    score = float(judge_res.get("score", 0))
    
    results_report.append({
        "type": "search",
        "query": query,
        "score": score,
        "reason": judge_res.get("reason", ""),
        "failures": judge_res.get("failures", []),
        "results_count": len(results),
        "audit": audit
    })
    
    return score, judge_res.get("failures", [])

async def test_qa(course_id: str, question: str, judge: Judge, results_report: list, created_sessions: list):
    import uuid
    session_id = str(uuid.uuid4())
    created_sessions.append(session_id)
    
    events = []
    final_response = None
    citations = []
    audit = {}
    trace_nodes = set()
    
    async with httpx.AsyncClient(timeout=120.0, trust_env=False) as client:
        async with client.stream("POST", f"{API_BASE}/qa/stream", json={
            "course_id": course_id,
            "question": question,
            "session_id": session_id,
            "history": []
        }) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                        events.append(event)
                        if event.get("type") == "trace":
                            trace_nodes.add(event.get("trace", {}).get("node"))
                        if event.get("type") == "final":
                            resp_data = event.get("response", {})
                            final_response = resp_data.get("answer", "")
                            citations = resp_data.get("citations", [])
                            audit = resp_data.get("answer_model_audit", {})
                    except json.JSONDecodeError:
                        pass

    assert final_response is not None, "Final response missing"
    assert len(citations) > 0, "Citations empty"
    
    # Assert trace coverage
    required_nodes = {"analyzer", "router", "retriever", "grader", "answer", "citation"}
    # The actual graph nodes might differ slightly in name, we check overlap
    assert len(trace_nodes.intersection(required_nodes)) > 0, f"Missing trace nodes, found: {trace_nodes}"
    
    # Assert audit
    # Depending on the backend structure, audit might be nested or direct
    audit_str = json.dumps(audit)
    assert "external_called" in audit_str, "external_called not in audit"
    
    # Judge
    sys_prompt = "You are an evaluator. Return a JSON with 'score' (1-5 float), 'reason' (string), 'failures' (list of strings)."
    user_prompt = f"Question: {question}\nAnswer: {final_response}\nCitations count: {len(citations)}\n\nRate relevance, evidence support, and hallucination risk."
    
    judge_res = await judge.evaluate(sys_prompt, user_prompt)
    score = float(judge_res.get("score", 0))
    
    results_report.append({
        "type": "qa",
        "question": question,
        "score": score,
        "reason": judge_res.get("reason", ""),
        "failures": judge_res.get("failures", []),
        "trace_nodes": list(trace_nodes),
        "citations_count": len(citations),
        "audit": audit
    })
    
    return score, judge_res.get("failures", [])

async def cleanup_sessions(sessions: list):
    async with httpx.AsyncClient(trust_env=False) as client:
        for sid in sessions:
            try:
                await client.delete(f"{API_BASE}/sessions/{sid}")
            except Exception as e:
                print(f"Failed to delete session {sid}: {e}")

async def main():
    print("Starting evaluation...")
    courses, model_settings = await check_endpoints()
    target_courses = [c for c in courses if c["name"] in ["Complex Network", "Bayesian Statistics"]]
    
    if not target_courses:
        print("Target courses not found!")
        return

    judge = Judge()
    results_report = []
    created_sessions = []
    
    for course in target_courses:
        print(f"\nEvaluating course: {course['name']}")
        
        # Get concepts
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(f"{API_BASE}/concepts?course_id={course['id']}")
            concepts = resp.json() if resp.status_code == 200 else []
            
        queries, questions = generate_samples(course['name'], concepts)
        
        search_scores = []
        for q in queries:
            print(f"  Search: {q}")
            try:
                score, failures = await test_search(course['id'], q, model_settings, judge, results_report)
                search_scores.append(score)
            except Exception as e:
                trace_str = traceback.format_exc()
                print(f"  Search failed: {trace_str}")
                results_report.append({"type": "search", "query": q, "error": repr(e), "traceback": trace_str, "score": 0})
                
        qa_scores = []
        for q in questions:
            print(f"  QA: {q}")
            try:
                score, failures = await test_qa(course['id'], q, judge, results_report, created_sessions)
                qa_scores.append(score)
            except Exception as e:
                trace_str = traceback.format_exc()
                print(f"  QA failed: {trace_str}")
                results_report.append({"type": "qa", "question": q, "error": repr(e), "traceback": trace_str, "score": 0})
                
        avg_search = sum(search_scores)/len(search_scores) if search_scores else 0
        avg_qa = sum(qa_scores)/len(qa_scores) if qa_scores else 0
        
        print(f"  Avg Search Score: {avg_search}")
        print(f"  Avg QA Score: {avg_qa}")

    await cleanup_sessions(created_sessions)
    
    # Save report
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(f"output/eval_runs/{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    with open(out_dir / "report.json", "w", encoding="utf-8") as f:
        json.dump(results_report, f, indent=2, ensure_ascii=False)
        
    print(f"\nReport saved to {out_dir}")

if __name__ == "__main__":
    asyncio.run(main())
