from __future__ import annotations

import os
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class Candidate(BaseModel):
    id: str
    text: str


class RerankRequest(BaseModel):
    query: str
    candidates: list[Candidate]
    model: str = DEFAULT_MODEL
    top_k: int | None = Field(default=None, ge=1)


class RerankResult(BaseModel):
    id: str
    score: float
    rank: int


class RerankResponse(BaseModel):
    model: str
    device: str
    results: list[RerankResult]


class ModelProvider:
    _lock = threading.Lock()
    _model: Any | None = None
    _model_name: str | None = None

    @classmethod
    def get(cls, model_name: str) -> Any:
        with cls._lock:
            if cls._model is not None and cls._model_name == model_name:
                return cls._model
            from sentence_transformers import CrossEncoder

            device = os.getenv("RERANKER_DEVICE", "cpu")
            max_length = int(os.getenv("RERANKER_MAX_LENGTH", "512"))
            cls._model = CrossEncoder(model_name, max_length=max_length, device=device)
            cls._model_name = model_name
            return cls._model


app = FastAPI(title="Text Reranker Runtime")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": os.getenv("RERANKER_MODEL", DEFAULT_MODEL),
        "device": os.getenv("RERANKER_DEVICE", "cpu"),
    }


@app.post("/rerank", response_model=RerankResponse)
def rerank(request: RerankRequest) -> RerankResponse:
    if not request.candidates:
        return RerankResponse(model=request.model, device=os.getenv("RERANKER_DEVICE", "cpu"), results=[])
    try:
        model = ModelProvider.get(request.model)
        pairs = [[request.query, candidate.text] for candidate in request.candidates]
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"reranker unavailable: {exc}") from exc

    ranked = sorted(
        (
            RerankResult(id=candidate.id, score=float(score), rank=0)
            for candidate, score in zip(request.candidates, scores)
        ),
        key=lambda item: item.score,
        reverse=True,
    )
    for idx, item in enumerate(ranked, start=1):
        item.rank = idx
    return RerankResponse(
        model=request.model,
        device=os.getenv("RERANKER_DEVICE", "cpu"),
        results=ranked,
    )
