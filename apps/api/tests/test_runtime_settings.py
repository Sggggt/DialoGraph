from __future__ import annotations


def test_env_sync_detects_bom_key(tmp_path, monkeypatch):
    from app.services import runtime_settings

    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    env_path.write_text("\ufeffDATABASE_URL=sqlite:///test.db\nRERANKER_ENABLED=true\n", encoding="utf-8")
    example_path.write_text("DATABASE_URL=\nRERANKER_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setattr(runtime_settings, "ENV_EXAMPLE_PATH", example_path)

    before = runtime_settings.env_sync_status()
    assert before["bom_keys"] == ["DATABASE_URL"]
    assert before["missing_keys"] == []

    runtime_settings.normalize_env_file()
    after = runtime_settings.env_sync_status()
    assert after["bom_keys"] == []
    assert "\ufeffDATABASE_URL" not in env_path.read_text(encoding="utf-8")


def test_env_sync_detects_key_mismatch(tmp_path, monkeypatch):
    from app.services import runtime_settings

    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    env_path.write_text("DATABASE_URL=sqlite:///test.db\nEXTRA_ONLY=true\n", encoding="utf-8")
    example_path.write_text("DATABASE_URL=\nOPENAI_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setattr(runtime_settings, "ENV_EXAMPLE_PATH", example_path)

    status = runtime_settings.env_sync_status()

    assert status["synced"] is False
    assert status["missing_keys"] == ["OPENAI_API_KEY"]
    assert status["extra_keys"] == ["EXTRA_ONLY"]


def test_runtime_check_skips_reranker_when_disabled(tmp_path, monkeypatch):
    from app.services import runtime_settings

    env_path = tmp_path / ".env"
    env_path.write_text("RERANKER_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)

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


def test_runtime_check_reports_reranker_status(tmp_path, monkeypatch):
    from app.services import runtime_settings
    from app.services import reranker

    env_path = tmp_path / ".env"
    env_path.write_text("RERANKER_ENABLED=true\nRERANKER_MODEL=unit-test-reranker\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setattr(reranker, "_reranker_instance", None)
    monkeypatch.setattr(reranker, "_reranker_error", None)

    class Settings:
        reranker_enabled = True
        reranker_model = "BAAI/bge-reranker-v2-m3"
        reranker_max_length = 512
        qdrant_url = "http://qdrant:6333"
        redis_url = "redis://redis:6379/0"
        openai_base_url = "https://api.openai.com/v1"

    monkeypatch.setattr(runtime_settings, "get_settings", lambda: Settings())
    monkeypatch.setattr(runtime_settings, "env_sync_status", lambda: {"synced": True, "missing_keys": [], "extra_keys": [], "bom_keys": []})
    monkeypatch.setattr(runtime_settings, "_check_postgres", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_qdrant", lambda: True)
    monkeypatch.setattr(runtime_settings, "_check_redis", lambda: True)

    def mock_load_reranker():
        raise RuntimeError("model not available")

    monkeypatch.setattr(reranker, "_load_reranker", mock_load_reranker)

    payload = runtime_settings.runtime_check_payload()
    # When reranker_enabled=True but model fails to load, enabled stays True
    # (config says enable it, but runtime cannot load it)
    assert payload["reranker"]["enabled"] is True
    assert payload["reranker"]["reachable"] is False
    assert payload["reranker"]["healthy"] is False


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


def test_settings_routes_compose_model_calls_through_bridge(monkeypatch, tmp_path):
    from app.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("MODEL_BRIDGE_ENABLED", "true")
    monkeypatch.setenv("MODEL_BRIDGE_PORT", "8766")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setenv("OPENAI_RESOLVE_IP", "1.2.3.4")
    monkeypatch.delenv("API_OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("API_OPENAI_RESOLVE_IP", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.openai_base_url == "http://host.docker.internal:8766"
    assert settings.openai_resolve_ip == "__none__"
    assert settings.model_bridge_enabled is True

    get_settings.cache_clear()


def test_update_model_settings_updates_current_process_env(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import runtime_settings

    env_path = tmp_path / ".env"
    env_path.write_text("EMBEDDING_MODEL=text-embedding-v4\n", encoding="utf-8")
    monkeypatch.setattr(runtime_settings, "ENV_PATH", env_path)
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v4")
    get_settings.cache_clear()

    payload = runtime_settings.update_model_settings({"embedding_model": "text-embedding-v3"})

    assert payload["embedding_model"] == "text-embedding-v3"
    assert env_path.read_text(encoding="utf-8").strip() == "EMBEDDING_MODEL=text-embedding-v3"
    assert get_settings().embedding_model == "text-embedding-v3"
