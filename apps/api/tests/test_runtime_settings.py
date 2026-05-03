from __future__ import annotations


def test_env_sync_detects_and_normalizes_bom_key(tmp_path, monkeypatch):
    from app.services import runtime_settings

    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    env_path.write_text("\ufeffDATABASE_URL=sqlite:///test.db\nRERANKER_ENABLED=true\n", encoding="utf-8")
    example_path.write_text("DATABASE_URL=\nRERANKER_ENABLED=true\nAPI_KEYS=\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setattr(runtime_settings, "ENV_EXAMPLE_PATH", example_path)

    before = runtime_settings.env_sync_status()
    assert before["bom_keys"] == ["DATABASE_URL"]
    assert before["missing_keys"] == ["API_KEYS"]

    runtime_settings.normalize_env_file()
    after = runtime_settings.env_sync_status()
    assert after["bom_keys"] == []
    assert after["missing_keys"] == []
    assert "\ufeffDATABASE_URL" not in env_path.read_text(encoding="utf-8")
    assert "API_KEYS=" in env_path.read_text(encoding="utf-8")


def test_runtime_check_skips_reranker_when_disabled(monkeypatch):
    from app.services import runtime_settings

    class Settings:
        reranker_enabled = False
        reranker_device = "cpu"
        reranker_model = "BAAI/bge-reranker-v2-m3"
        reranker_url = "http://reranker:8080/rerank"
        qdrant_url = "http://qdrant:6333"
        redis_url = "redis://redis:6379/0"
        openai_base_url = "https://api.openai.com/v1"

    monkeypatch.setattr(runtime_settings, "get_settings", lambda: Settings())
    monkeypatch.setattr(runtime_settings, "env_sync_status", lambda: {"synced": True, "missing_keys": [], "extra_keys": [], "bom_keys": []})
    monkeypatch.setattr(runtime_settings, "_check_postgres", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_qdrant", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_redis", lambda: True)

    def fail_http(*args, **kwargs):
        raise AssertionError("reranker health should not be called")

    monkeypatch.setattr(runtime_settings.httpx, "get", fail_http)

    payload = runtime_settings.runtime_check_payload()
    assert payload["reranker"]["enabled"] is False
    assert payload["blocking_issues"] == []


def test_runtime_check_blocks_unreachable_reranker(monkeypatch):
    from app.services import runtime_settings

    class Settings:
        reranker_enabled = True
        reranker_device = "cpu"
        reranker_model = "BAAI/bge-reranker-v2-m3"
        reranker_url = "http://reranker:8080/rerank"
        qdrant_url = "http://qdrant:6333"
        redis_url = "redis://redis:6379/0"
        openai_base_url = "https://api.openai.com/v1"

    monkeypatch.setattr(runtime_settings, "get_settings", lambda: Settings())
    monkeypatch.setattr(runtime_settings, "env_sync_status", lambda: {"synced": True, "missing_keys": [], "extra_keys": [], "bom_keys": []})
    monkeypatch.setattr(runtime_settings, "_check_postgres", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_qdrant", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_redis", lambda: True)

    def raise_http(*args, **kwargs):
        raise RuntimeError("not available")

    monkeypatch.setattr(runtime_settings.httpx, "get", raise_http)

    payload = runtime_settings.runtime_check_payload(require_reranker=True)
    assert payload["blocking_issues"]
    assert payload["blocking_issues"][0]["code"] == "reranker_unreachable"


def test_runtime_check_reports_model_bridge_when_configured(monkeypatch):
    from app.services import runtime_settings

    class Settings:
        reranker_enabled = False
        reranker_device = "cpu"
        reranker_model = "BAAI/bge-reranker-v2-m3"
        reranker_url = "http://reranker:8080/rerank"
        qdrant_url = "http://qdrant:6333"
        redis_url = "redis://redis:6379/0"
        openai_base_url = "http://host.docker.internal:8765"

    class Response:
        status_code = 200

    monkeypatch.setattr(runtime_settings, "get_settings", lambda: Settings())
    monkeypatch.setattr(runtime_settings, "env_sync_status", lambda: {"synced": True, "missing_keys": [], "extra_keys": [], "bom_keys": []})
    monkeypatch.setattr(runtime_settings, "_check_postgres", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_qdrant", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_redis", lambda: True)
    monkeypatch.setattr(runtime_settings.httpx, "get", lambda *args, **kwargs: Response())

    payload = runtime_settings.runtime_check_payload()

    assert payload["infrastructure"]["model_bridge"] is True
    assert payload["warnings"] == []


def test_update_model_settings_updates_current_process_env(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import runtime_settings

    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    env_path.write_text("RERANKER_ENABLED=true\n", encoding="utf-8")
    example_path.write_text("RERANKER_ENABLED=true\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setattr(runtime_settings, "ENV_EXAMPLE_PATH", example_path)
    monkeypatch.setenv("RERANKER_ENABLED", "true")
    get_settings.cache_clear()

    payload = runtime_settings.update_model_settings({"reranker_enabled": False})

    assert payload["reranker_enabled"] is False
    assert env_path.read_text(encoding="utf-8").strip() == "RERANKER_ENABLED=false"
    assert get_settings().reranker_enabled is False
