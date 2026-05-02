from __future__ import annotations

import threading
from typing import Any

import httpx

from app.core.config import get_settings


class RerankerUnavailableError(RuntimeError):
    pass


class RerankerProvider:
    _instance: "RerankerProvider | None" = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> "RerankerProvider":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instance = None

    def __init__(self) -> None:
        settings = get_settings()
        self.url = settings.reranker_url
        self.model = settings.reranker_model
        self.timeout = httpx.Timeout(60.0, connect=5.0)

    def rerank(self, query: str, candidates: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if not candidates:
            return []
        settings = get_settings()
        payload = {
            "model": self.model,
            "query": query,
            "top_k": top_k,
            "candidates": [
                {
                    "id": item["chunk_id"],
                    "text": (item.get("content") or item.get("snippet") or "")[: settings.reranker_text_chars],
                }
                for item in candidates
            ],
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(self.url, json=payload)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            raise RerankerUnavailableError(f"Reranker service unavailable at {self.url}: {exc}") from exc

        scores_by_id = {str(item["id"]): float(item["score"]) for item in data.get("results", [])}
        if len(scores_by_id) != len(candidates):
            raise RerankerUnavailableError(
                f"Reranker service returned {len(scores_by_id)} scores for {len(candidates)} candidates"
            )
        for item in candidates:
            score_value = scores_by_id[str(item["chunk_id"])]
            item.setdefault("metadata", {}).setdefault("scores", {})["rerank"] = score_value
            item["score"] = score_value
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[:top_k]
