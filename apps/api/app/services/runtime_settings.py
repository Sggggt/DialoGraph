from __future__ import annotations

from pathlib import Path

from app.core.config import WORKSPACE_ROOT, get_settings


ENV_PATH = WORKSPACE_ROOT / ".env"


def model_settings_payload() -> dict:
    settings = get_settings()
    return {
        "provider": "openai_compatible",
        "base_url": settings.openai_base_url,
        "resolve_ip": settings.openai_resolve_ip,
        "embedding_model": settings.embedding_model,
        "chat_model": settings.chat_model,
        "embedding_dimensions": settings.embedding_dimensions,
        "graph_extraction_chunk_limit": settings.graph_extraction_chunk_limit,
        "graph_extraction_chunks_per_document": settings.graph_extraction_chunks_per_document,
        "has_api_key": bool(settings.openai_api_key),
        "degraded_mode": not settings.openai_api_key,
    }


def _serialize_env_value(value: str | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text or any(char.isspace() for char in text) or any(char in text for char in ['"', "#", "="]):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def _update_env_file(updates: dict[str, str | int | bool | None]) -> None:
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = {key.upper(): value for key, value in updates.items()}
    next_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip().upper()
        if key not in remaining:
            next_lines.append(line)
            continue
        value = remaining.pop(key)
        if value is not None:
            next_lines.append(f"{key}={_serialize_env_value(value)}")

    if remaining:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        for key, value in remaining.items():
            if value is not None:
                next_lines.append(f"{key}={_serialize_env_value(value)}")

    ENV_PATH.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def update_model_settings(payload: dict) -> dict:
    updates: dict[str, str | int | bool | None] = {}
    key_map = {
        "base_url": "openai_base_url",
        "resolve_ip": "openai_resolve_ip",
        "embedding_model": "embedding_model",
        "chat_model": "chat_model",
        "embedding_dimensions": "embedding_dimensions",
        "graph_extraction_chunk_limit": "graph_extraction_chunk_limit",
        "graph_extraction_chunks_per_document": "graph_extraction_chunks_per_document",
    }
    for key, env_key in key_map.items():
        value = payload.get(key)
        if value is not None:
            if key == "resolve_ip" and isinstance(value, str) and not value.strip():
                updates[env_key] = None
            else:
                updates[env_key] = value.strip() if isinstance(value, str) else value

    api_key = payload.get("api_key")
    if payload.get("clear_api_key"):
        updates["openai_api_key"] = None
    elif isinstance(api_key, str) and api_key.strip():
        updates["openai_api_key"] = api_key.strip()

    if updates:
        _update_env_file(updates)
        get_settings.cache_clear()
        get_settings()
    return model_settings_payload()
