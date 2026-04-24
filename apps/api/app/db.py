from collections.abc import Generator

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()


def candidate_sqlite_paths() -> list[Path]:
    apps_root = Path(__file__).resolve().parents[2]
    candidates = [
        apps_root / "course_kg.db",
        apps_root / "knowledge_base.db",
    ]
    seen: set[Path] = set()
    ordered: list[Path] = []
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def try_connect(engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def has_materialized_course_data(engine) -> bool:
    try:
        with engine.connect() as connection:
            document_count = connection.execute(text("SELECT COUNT(*) FROM documents")).scalar() or 0
            concept_count = connection.execute(text("SELECT COUNT(*) FROM concepts")).scalar() or 0
        return bool(document_count or concept_count)
    except Exception:
        return False


def build_engine():
    database_url = settings.database_url
    connect_args = {"connect_timeout": 5} if database_url.startswith("postgresql") else {}
    primary = create_engine(database_url, future=True, echo=False, connect_args=connect_args)
    if try_connect(primary):
        for sqlite_path in candidate_sqlite_paths():
            if not sqlite_path.exists():
                continue
            fallback = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True, echo=False)
            if try_connect(fallback) and has_materialized_course_data(fallback) and not has_materialized_course_data(primary):
                return fallback
        return primary

    for sqlite_path in candidate_sqlite_paths():
        fallback = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True, echo=False)
        if try_connect(fallback):
            return fallback

    raise RuntimeError("No available database engine could be initialized")


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


SCHEMA_PATCHES: dict[str, dict[str, str]] = {
    "concepts": {
        "normalized_name": "TEXT",
        "concept_type": "VARCHAR(64) DEFAULT 'concept'",
        "importance_score": "FLOAT DEFAULT 0",
    },
    "concept_relations": {
        "confidence": "FLOAT DEFAULT 0.55",
        "extraction_method": "VARCHAR(64) DEFAULT 'heuristic'",
        "is_validated": "BOOLEAN DEFAULT 0",
    },
    "ingestion_jobs": {
        "batch_id": "VARCHAR(36)",
        "source_path": "TEXT",
    },
    "qa_sessions": {
        "title": "VARCHAR(255)",
        "last_question": "TEXT",
        "last_answer": "TEXT",
        "transcript": "JSON DEFAULT '[]'",
    },
    "agent_runs": {
        "session_id": "VARCHAR(36)",
        "route": "VARCHAR(64)",
        "current_node": "VARCHAR(64)",
        "retry_count": "INTEGER DEFAULT 0",
        "final_answer": "TEXT",
        "error_message": "TEXT",
        "metadata_json": "JSON DEFAULT '{}'",
        "started_at": "DATETIME",
        "completed_at": "DATETIME",
    },
    "agent_trace_events": {
        "document_ids": "JSON DEFAULT '[]'",
        "scores": "JSON DEFAULT '{}'",
        "duration_ms": "INTEGER DEFAULT 0",
        "error_message": "TEXT",
    },
}


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, patch_columns in SCHEMA_PATCHES.items():
            if table_name not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_sql in patch_columns.items():
                if column_name in existing:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
