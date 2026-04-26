from functools import lru_cache
from pathlib import Path
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

    course_name: str = "Sample Course"
    data_root: Path = Field(default=WORKSPACE_ROOT / "data")
    storage_root: Path | None = None
    ingestion_root: Path | None = None

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_resolve_ip: str | None = None
    embedding_model: str = "text-embedding-v4"
    chat_model: str = "qwen-plus"
    embedding_dimensions: int = 1024
    enable_model_fallback: bool = False

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
    settings.data_root.mkdir(parents=True, exist_ok=True)
    settings.course_data_root_path.mkdir(parents=True, exist_ok=True)
    settings.storage_root_path.mkdir(parents=True, exist_ok=True)
    settings.ingestion_root_path.mkdir(parents=True, exist_ok=True)
    return settings
