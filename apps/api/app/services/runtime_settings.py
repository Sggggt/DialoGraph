from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import text

from app.core.config import WORKSPACE_ROOT, get_settings


ENV_PATH = WORKSPACE_ROOT / ".env"
ENV_EXAMPLE_PATH = WORKSPACE_ROOT / ".env.example"


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
        "reranker_enabled": settings.reranker_enabled,
        "reranker_model": settings.reranker_model,
        "reranker_device": settings.reranker_device,
        "reranker_url": settings.reranker_url,
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
    seen_keys: set[str] = set()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip().lstrip("\ufeff").upper()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if key not in remaining:
            value = line.split("=", 1)[1]
            next_lines.append(f"{key}={value}")
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


def _env_keys(path: Path) -> tuple[set[str], list[str]]:
    keys: set[str] = set()
    bom_keys: list[str] = []
    if not path.exists():
        return keys, bom_keys
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        raw_key = raw_line.split("=", 1)[0].strip()
        clean_key = raw_key.lstrip("\ufeff").upper()
        if raw_key.startswith("\ufeff"):
            bom_keys.append(clean_key)
        keys.add(clean_key)
    return keys, bom_keys


def _env_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        raw_key, value = raw_line.split("=", 1)
        key = raw_key.strip().lstrip("\ufeff").upper()
        entries.setdefault(key, value)
    return entries


def normalize_env_file() -> None:
    actual = _env_entries(ENV_PATH)
    example = _env_entries(ENV_EXAMPLE_PATH)
    merged = dict(actual)
    for key, value in example.items():
        merged.setdefault(key, value)
    if not merged:
        return
    lines = [f"{key}={value}" for key, value in merged.items()]
    ENV_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def env_sync_status() -> dict:
    example_keys, _ = _env_keys(ENV_EXAMPLE_PATH)
    actual_keys, bom_keys = _env_keys(ENV_PATH)
    missing = sorted(example_keys - actual_keys)
    extra = sorted(actual_keys - example_keys)
    return {
        "synced": not missing and not bom_keys,
        "missing_keys": missing,
        "extra_keys": extra,
        "bom_keys": sorted(set(bom_keys)),
    }


def _runtime_issue(code: str, title: str, message: str, fix_commands: list[str] | None = None) -> dict:
    return {"code": code, "title": title, "message": message, "fix_commands": fix_commands or []}


def _check_postgres() -> bool:
    with suppress(Exception):
        import app.db as db

        with db.SessionLocal() as session:
            session.execute(text("SELECT 1"))
            return True
    return False


def _check_qdrant() -> bool:
    settings = get_settings()
    with suppress(Exception):
        response = httpx.get(f"{settings.qdrant_url.rstrip('/')}/collections", timeout=2.0)
        return response.status_code < 500
    return False


def _check_redis() -> bool:
    settings = get_settings()
    parsed = urlparse(settings.redis_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    with suppress(Exception):
        import socket

        with socket.create_connection((host, port), timeout=2.0) as sock:
            sock.sendall(b"PING\r\n")
            return sock.recv(16).startswith(b"+PONG")
    return False


def runtime_check_payload(
    require_reranker: bool | None = None,
    expected_model: str | None = None,
    expected_device: str | None = None,
    expected_url: str | None = None,
) -> dict:
    settings = get_settings()
    reranker_model = expected_model or settings.reranker_model
    reranker_device = expected_device or settings.reranker_device
    reranker_url = expected_url or settings.reranker_url
    env_sync = env_sync_status()
    blocking_issues: list[dict] = []
    warnings: list[dict] = []
    if env_sync["bom_keys"]:
        blocking_issues.append(
            _runtime_issue(
                "env_bom_keys",
                ".env contains BOM-prefixed keys",
                "One or more .env keys contain a UTF-8 BOM prefix and must be normalized before saving settings.",
                ["Open the Settings page and save once, or rewrite the affected key without the BOM prefix."],
            )
        )
    if env_sync["missing_keys"]:
        blocking_issues.append(
            _runtime_issue(
                "env_missing_keys",
                ".env is missing keys from .env.example",
                "The runtime environment is missing required configuration keys.",
                ["Compare .env with .env.example and add the missing keys."],
            )
        )
    if env_sync["extra_keys"]:
        warnings.append(
            _runtime_issue(
                "env_extra_keys",
                ".env has extra keys",
                "Extra keys are ignored by the application unless explicitly supported.",
            )
        )

    reranker_required = settings.reranker_enabled if require_reranker is None else require_reranker
    reranker_status = {
        "enabled": settings.reranker_enabled,
        "device": reranker_device,
        "model": reranker_model,
        "url": reranker_url,
        "reachable": False,
        "healthy": False,
        "reported_model": None,
        "reported_device": None,
        "model_matches": None,
        "device_matches": None,
    }
    if reranker_required:
        health_url = reranker_url.rsplit("/", 1)[0].rstrip("/") + "/health"
        with suppress(Exception):
            response = httpx.get(health_url, timeout=3.0)
            reranker_status["reachable"] = True
            reranker_status["healthy"] = response.status_code == 200
            if response.headers.get("content-type", "").startswith("application/json"):
                data = response.json()
                reranker_status["reported_model"] = data.get("model")
                reranker_status["reported_device"] = data.get("device")
                reranker_status["model_matches"] = data.get("model") == reranker_model
                reranker_status["device_matches"] = data.get("device") == reranker_device
        if not reranker_status["reachable"] or not reranker_status["healthy"]:
            blocking_issues.append(
                _runtime_issue(
                    "reranker_unreachable",
                    "Reranker runtime is not reachable",
                    "Reranker is enabled, but the HTTP runtime did not respond successfully.",
                    [
                        ".\\start-app.ps1",
                        "docker compose -f infra/docker-compose.yml --profile reranker-cpu up -d reranker-cpu",
                    ],
                )
            )
        elif reranker_status["model_matches"] is False or reranker_status["device_matches"] is False:
            blocking_issues.append(
                _runtime_issue(
                    "reranker_mismatch",
                    "Reranker runtime does not match .env",
                    "The running reranker reports a different model or device than the application settings.",
                    [".\\start-app.ps1"],
                )
            )

    infrastructure = {
        "postgres": _check_postgres(),
        "qdrant": _check_qdrant(),
        "redis": _check_redis(),
    }
    for key, ok in infrastructure.items():
        if not ok:
            warnings.append(
                _runtime_issue(
                    f"{key}_unreachable",
                    f"{key} is not reachable",
                    f"The {key} infrastructure check failed from the API process.",
                    [".\\start-app.ps1"],
                )
            )
    return {
        "env_sync": env_sync,
        "reranker": reranker_status,
        "infrastructure": infrastructure,
        "blocking_issues": blocking_issues,
        "warnings": warnings,
    }


def update_model_settings(payload: dict) -> dict:
    normalize_env_file()
    updates: dict[str, str | int | bool | None] = {}
    key_map = {
        "base_url": "openai_base_url",
        "resolve_ip": "openai_resolve_ip",
        "embedding_model": "embedding_model",
        "chat_model": "chat_model",
        "embedding_dimensions": "embedding_dimensions",
        "graph_extraction_chunk_limit": "graph_extraction_chunk_limit",
        "graph_extraction_chunks_per_document": "graph_extraction_chunks_per_document",
        "reranker_enabled": "reranker_enabled",
        "reranker_model": "reranker_model",
        "reranker_device": "reranker_device",
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
