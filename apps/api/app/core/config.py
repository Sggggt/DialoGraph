from functools import lru_cache
from pathlib import Path
import os
import re

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = APP_DIR.parents[1]
INVALID_COURSE_DIR_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(WORKSPACE_ROOT / ".env", APP_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Course Knowledge Base API"
    app_env: str = "development"
    app_port: int = 8000

    database_url: str = "sqlite:///./knowledge_base.db"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "knowledge_chunks"
    redis_url: str = "redis://localhost:6379/0"
    enable_database_fallback: bool = False
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    api_keys: str = ""

    course_name: str = "Sample Course"
    data_root: Path = Field(default=WORKSPACE_ROOT / "data")
    storage_root: Path | None = None
    ingestion_root: Path | None = None

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_resolve_ip: str | None = None
    model_bridge_enabled: bool = False
    model_bridge_port: int = 8765
    embedding_model: str = "text-embedding-v4"
    chat_model: str = "qwen-plus"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = Field(default=10, ge=1, le=10)
    graph_extraction_chunk_limit: int = Field(default=72, ge=1, le=200)
    graph_extraction_chunks_per_document: int = Field(default=2, ge=1, le=10)
    enable_model_fallback: bool = False
    retrieval_recall_k_default: int = Field(default=64, ge=1, le=200)
    retrieval_recall_k_formula: int = Field(default=80, ge=1, le=200)
    reranker_enabled: bool = False
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    reranker_max_length: int = Field(default=512, ge=64, le=2048)
    semantic_chunking_enabled: bool = True
    semantic_chunking_min_length: int = Field(default=2000, ge=500, le=5000)
    model_cache_root: Path = Field(default=WORKSPACE_ROOT / "models" / "huggingface")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def api_key_list(self) -> list[str]:
        return [key.strip() for key in self.api_keys.split(",") if key.strip()]

    def sanitize_course_dir_name(self, course_name: str) -> str:
        value = INVALID_COURSE_DIR_CHARS.sub("-", course_name).strip()
        value = re.sub(r"\s+", " ", value).rstrip(".")
        return value or "Course"

    def course_paths_for_name(self, course_name: str) -> dict[str, Path]:
        course_root = self.data_root / self.sanitize_course_dir_name(course_name)
        return {
            "course_root": course_root,
            "storage_root": course_root / "storage",
            "ingestion_root": course_root / "ingestion",
        }

    @property
    def course_data_root_path(self) -> Path:
        return self.course_paths_for_name(self.course_name)["course_root"]

    @property
    def storage_root_path(self) -> Path:
        return Path(self.storage_root) if self.storage_root else self.course_paths_for_name(self.course_name)["storage_root"]

    @property
    def ingestion_root_path(self) -> Path:
        return Path(self.ingestion_root) if self.ingestion_root else self.course_paths_for_name(self.course_name)["ingestion_root"]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    env_entries: dict[str, str] = {}
    env_path = WORKSPACE_ROOT / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in raw_line:
                continue
            key, value = raw_line.split("=", 1)
            env_entries[key.strip().lstrip("\ufeff").upper()] = value

    api_base_url = os.getenv("API_OPENAI_BASE_URL")
    api_resolve_ip = os.getenv("API_OPENAI_RESOLVE_IP")
    model_bridge_enabled = str(os.getenv("MODEL_BRIDGE_ENABLED") or env_entries.get("MODEL_BRIDGE_ENABLED", "")).lower() in {
        "true",
        "1",
        "yes",
        "on",
    }
    model_bridge_port = os.getenv("MODEL_BRIDGE_PORT") or env_entries.get("MODEL_BRIDGE_PORT")
    if model_bridge_port:
        try:
            settings.model_bridge_port = int(model_bridge_port)
        except ValueError:
            pass
    settings.model_bridge_enabled = model_bridge_enabled
    if api_base_url:
        settings.openai_base_url = api_base_url
    elif model_bridge_enabled:
        settings.openai_base_url = f"http://host.docker.internal:{settings.model_bridge_port}"
        settings.openai_resolve_ip = "__none__"
    elif settings.openai_base_url == "https://api.openai.com/v1" and env_entries.get("OPENAI_BASE_URL"):
        settings.openai_base_url = env_entries["OPENAI_BASE_URL"]
    if api_resolve_ip is not None:
        settings.openai_resolve_ip = api_resolve_ip
    elif model_bridge_enabled:
        settings.openai_resolve_ip = "__none__"
    elif env_entries.get("OPENAI_RESOLVE_IP") is not None:
        settings.openai_resolve_ip = env_entries.get("OPENAI_RESOLVE_IP")

    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.course_data_root_path.mkdir(parents=True, exist_ok=True)
    settings.storage_root_path.mkdir(parents=True, exist_ok=True)
    settings.ingestion_root_path.mkdir(parents=True, exist_ok=True)
    return settings
