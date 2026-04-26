from __future__ import annotations

import json
import math
from pathlib import Path
from threading import Lock
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as rest

from app.core.config import get_settings


_VECTOR_FILE_LOCKS: dict[Path, Lock] = {}
_VECTOR_FILE_LOCKS_GUARD = Lock()


def vector_file_lock(path: Path) -> Lock:
    resolved = path.resolve()
    with _VECTOR_FILE_LOCKS_GUARD:
        if resolved not in _VECTOR_FILE_LOCKS:
            _VECTOR_FILE_LOCKS[resolved] = Lock()
        return _VECTOR_FILE_LOCKS[resolved]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    left_mag = math.sqrt(sum(a * a for a in left)) or 1.0
    right_mag = math.sqrt(sum(b * b for b in right)) or 1.0
    return numerator / (left_mag * right_mag)


class FallbackVectorStore:
    def __init__(self, backing_file: Path) -> None:
        self.backing_file = backing_file
        self.backing_file.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> list[dict]:
        if not self.backing_file.exists():
            return []
        text = self.backing_file.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                data, _ = json.JSONDecoder().raw_decode(text)
            except json.JSONDecodeError:
                return []
        return data if isinstance(data, list) else []

    def _write(self, data: list[dict]) -> None:
        temporary_file = self.backing_file.with_suffix(f"{self.backing_file.suffix}.tmp")
        temporary_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary_file.replace(self.backing_file)

    def upsert(self, points: list[dict]) -> None:
        with vector_file_lock(self.backing_file):
            current = self._read()
            indexed = {item["id"]: item for item in current}
            for point in points:
                indexed[point["id"]] = point
            self._write(list(indexed.values()))

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        id_set = set(ids)
        with vector_file_lock(self.backing_file):
            current = self._read()
            self._write([item for item in current if item.get("id") not in id_set])

    def search(self, vector: list[float], limit: int, filters: dict[str, Any]) -> list[dict]:
        with vector_file_lock(self.backing_file):
            points = self._read()
        results = []
        for point in points:
            payload = point.get("payload", {})
            if any(filters.get(key) and payload.get(key) != filters[key] for key in filters):
                continue
            score = cosine_similarity(vector, point["vector"])
            results.append({"id": point["id"], "score": score, "payload": payload})
        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]


class VectorStore:
    def __init__(self, course_name: str | None = None) -> None:
        self.settings = get_settings()
        self.collection = self.settings.qdrant_collection
        course_paths = self.settings.course_paths_for_name(course_name or self.settings.course_name)
        self.fallback = FallbackVectorStore(course_paths["ingestion_root"] / "vector_index.json")
        self.client: QdrantClient | None = None
        try:
            self.client = QdrantClient(url=self.settings.qdrant_url, timeout=5.0)
            self._ensure_collection()
        except Exception:
            if not self.settings.enable_model_fallback:
                raise
            self.client = None

    def _ensure_collection(self) -> None:
        if not self.client:
            return
        collections = {item.name for item in self.client.get_collections().collections}
        if self.collection not in collections:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=rest.VectorParams(
                    size=self.settings.embedding_dimensions,
                    distance=rest.Distance.COSINE,
                ),
            )

    def upsert(self, points: list[dict]) -> None:
        if not points:
            return
        if self.client:
            self.client.upsert(
                collection_name=self.collection,
                points=[
                    rest.PointStruct(id=point["id"], vector=point["vector"], payload=point["payload"])
                    for point in points
                ],
            )
        else:
            if not self.settings.enable_model_fallback:
                raise RuntimeError("Qdrant is unavailable and ENABLE_MODEL_FALLBACK is false")
            self.fallback.upsert(points)

    def delete(self, ids: list[str]) -> None:
        if not ids:
            return
        if self.client:
            self.client.delete(
                collection_name=self.collection,
                points_selector=rest.PointIdsList(points=ids),
            )
        if self.settings.enable_model_fallback:
            self.fallback.delete(ids)

    def search(self, vector: list[float], limit: int, filters: dict[str, Any] | None = None) -> list[dict]:
        filters = {key: value for key, value in (filters or {}).items() if value not in (None, "", [], {})}
        if self.client:
            qdrant_filter = rest.Filter(
                must=[rest.FieldCondition(key=key, match=rest.MatchValue(value=value)) for key, value in filters.items()]
            ) if filters else None
            if hasattr(self.client, "query_points"):
                response = self.client.query_points(
                    collection_name=self.collection,
                    query=vector,
                    limit=limit,
                    query_filter=qdrant_filter,
                    with_payload=True,
                )
                results = response.points
            else:
                results = self.client.search(
                    collection_name=self.collection,
                    query_vector=vector,
                    limit=limit,
                    query_filter=qdrant_filter,
                )
            return [{"id": item.id, "score": item.score, "payload": item.payload} for item in results]
        if not self.settings.enable_model_fallback:
            raise RuntimeError("Qdrant is unavailable and ENABLE_MODEL_FALLBACK is false")
        return self.fallback.search(vector=vector, limit=limit, filters=filters)
