from __future__ import annotations

import queue
from collections import defaultdict
from datetime import datetime
from typing import Any


TERMINAL_LOG_EVENTS = {"batch_completed", "batch_failed", "batch_partial_failed"}
_HISTORY_LIMIT = 500
_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
_subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = defaultdict(list)


def emit_ingestion_log(batch_id: str | None, event: str, message: str, **payload: Any) -> None:
    if not batch_id:
        return
    item = {
        "timestamp": datetime.utcnow().isoformat(),
        "event": event,
        "message": message,
        **payload,
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
    return list(_history.get(batch_id, [])), subscriber


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
