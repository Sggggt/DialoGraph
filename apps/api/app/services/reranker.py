from __future__ import annotations

import os
import threading
from typing import Any

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
        os.environ.setdefault("HF_HOME", str(settings.model_cache_root))
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:
            raise RerankerUnavailableError(
                "sentence-transformers is required for reranking; install the rerank extra"
            ) from exc
        try:
            self.model = CrossEncoder(settings.reranker_model, max_length=settings.reranker_max_length, device=settings.reranker_device)
        except Exception as exc:
            raise RerankerUnavailableError(f"Failed to load reranker model {settings.reranker_model}: {exc}") from exc

    def rerank(self, query: str, candidates: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        if not candidates:
            return []
        settings = get_settings()
        pairs = [[query, (item.get("content") or item.get("snippet") or "")[: settings.reranker_text_chars]] for item in candidates]
        scores = self.model.predict(pairs, show_progress_bar=False)
        for item, score in zip(candidates, scores):
            score_value = float(score)
            item.setdefault("metadata", {}).setdefault("scores", {})["rerank"] = score_value
            item["score"] = score_value
        return sorted(candidates, key=lambda item: item["score"], reverse=True)[:top_k]
