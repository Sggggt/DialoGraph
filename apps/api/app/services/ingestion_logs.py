from __future__ import annotations

import queue
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any


TERMINAL_LOG_EVENTS = {"batch_completed", "batch_failed", "batch_partial_failed", "batch_skipped", "batch_missing"}
_HISTORY_LIMIT = 500
_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
_subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = defaultdict(list)


def _jsonable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    import json

    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def _serialize_log(log) -> dict[str, Any]:
    payload = log.payload_json or {}
    return {
        "log_id": log.id,
        "timestamp": log.created_at.isoformat(),
        "event": log.event,
        "message": log.message,
        **payload,
    }


def _persist_ingestion_log(batch_id: str, event: str, message: str, payload: dict[str, Any], created_at: datetime) -> str | None:
    try:
        from app.db import SessionLocal
        from app.models import IngestionLog

        with SessionLocal() as session:
            log = IngestionLog(
                id=str(uuid.uuid4()),
                batch_id=batch_id,
                event=event,
                message=message,
                payload_json=_jsonable_payload(payload),
                created_at=created_at,
            )
            session.add(log)
            session.commit()
            return log.id
    except Exception:
        return None


def list_ingestion_logs(batch_id: str, limit: int = _HISTORY_LIMIT) -> list[dict[str, Any]]:
    try:
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import IngestionLog

        with SessionLocal() as session:
            rows = session.scalars(
                select(IngestionLog)
                .where(IngestionLog.batch_id == batch_id)
                .order_by(IngestionLog.created_at.desc(), IngestionLog.id.desc())
                .limit(limit)
            ).all()
            return [_serialize_log(log) for log in reversed(rows)]
    except Exception:
        return list(_history.get(batch_id, []))


def emit_ingestion_log(batch_id: str | None, event: str, message: str, **payload: Any) -> None:
    if not batch_id:
        return
    created_at = datetime.utcnow()
    persisted_id = _persist_ingestion_log(batch_id, event, message, payload, created_at)
    item = {
        "log_id": persisted_id,
        "timestamp": created_at.isoformat(),
        "event": event,
        "message": message,
        **_jsonable_payload(payload),
    }
    history = _history[batch_id]
    history.append(item)
    if len(history) > _HISTORY_LIMIT:
        del history[:-_HISTORY_LIMIT]
    for subscriber in list(_subscribers[batch_id]):
        subscriber.put(item)


def subscribe_ingestion_logs(batch_id: str) -> tuple[list[dict[str, Any]], queue.Queue[dict[str, Any]]]:
    subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
    _subscribers[batch_id].append(subscriber)
    return list_ingestion_logs(batch_id), subscriber


def unsubscribe_ingestion_logs(batch_id: str, subscriber: queue.Queue[dict[str, Any]]) -> None:
    subscribers = _subscribers.get(batch_id)
    if not subscribers:
        return
    try:
        subscribers.remove(subscriber)
    except ValueError:
        return
    if not subscribers:
        _subscribers.pop(batch_id, None)
