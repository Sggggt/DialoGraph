from collections.abc import Generator

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.sql.compiler import IdentifierPreparer
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
        if settings.enable_database_fallback:
            for sqlite_path in candidate_sqlite_paths():
                if not sqlite_path.exists():
                    continue
                fallback = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True, echo=False)
                if try_connect(fallback) and has_materialized_course_data(fallback) and not has_materialized_course_data(primary):
                    return fallback
        return primary

    if not settings.enable_database_fallback:
        raise RuntimeError("Primary database is unavailable and ENABLE_DATABASE_FALLBACK is false")

    for sqlite_path in candidate_sqlite_paths():
        fallback = create_engine(f"sqlite:///{sqlite_path.as_posix()}", future=True, echo=False)
        if try_connect(fallback):
            return fallback

    raise RuntimeError("No available database engine could be initialized")


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


SCHEMA_PATCHES: dict[str, dict[str, str]] = {
    "chunks": {
        "parent_chunk_id": "VARCHAR(36)",
        "summary": "TEXT",
        "keywords": "JSON DEFAULT '[]'",
        "embedding_text_version": "VARCHAR(32) DEFAULT 'metadata_enriched_v1'",
    },
    "concepts": {
        "normalized_name": "TEXT",
        "concept_type": "VARCHAR(64) DEFAULT 'concept'",
        "importance_score": "FLOAT DEFAULT 0",
        "evidence_count": "INTEGER DEFAULT 0",
        "community_louvain": "INTEGER",
        "community_spectral": "INTEGER",
        "component_id": "INTEGER",
        "centrality_json": "JSON DEFAULT '{}'",
        "graph_rank_score": "FLOAT DEFAULT 0",
        "source_document_ids": "JSON DEFAULT '[]'",
        "quality_json": "JSON DEFAULT '{}'",
    },
    "concept_relations": {
        "confidence": "FLOAT DEFAULT 0.55",
        "extraction_method": "VARCHAR(64) DEFAULT 'heuristic'",
        "is_validated": "BOOLEAN DEFAULT false",
        "weight": "FLOAT DEFAULT 0",
        "semantic_similarity": "FLOAT DEFAULT 0",
        "support_count": "INTEGER DEFAULT 1",
        "relation_source": "VARCHAR(64) DEFAULT 'llm'",
        "is_inferred": "BOOLEAN DEFAULT false",
        "metadata_json": "JSON DEFAULT '{}'",
        "source_document_ids": "JSON DEFAULT '[]'",
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
    "quality_profiles": {
        "sample_chunk_ids": "JSON DEFAULT '[]'",
        "is_active": "BOOLEAN DEFAULT true",
    },
    "graph_relation_candidates": {
        "decision_json": "JSON DEFAULT '{}'",
        "metadata_json": "JSON DEFAULT '{}'",
        "source_document_ids": "JSON DEFAULT '[]'",
    },
    "graph_community_summaries": {
        "key_concepts_json": "JSON DEFAULT '[]'",
        "representative_chunk_ids": "JSON DEFAULT '[]'",
        "source_document_ids": "JSON DEFAULT '[]'",
        "quality_json": "JSON DEFAULT '{}'",
        "is_active": "BOOLEAN DEFAULT true",
    },
    "graph_extraction_runs": {
        "coverage_json": "JSON DEFAULT '{}'",
        "budget_json": "JSON DEFAULT '{}'",
        "stats_json": "JSON DEFAULT '{}'",
        "error_message": "TEXT",
        "started_at": "DATETIME",
        "completed_at": "DATETIME",
    },
    "graph_extraction_chunk_tasks": {
        "selected_reason": "JSON DEFAULT '{}'",
        "payload_json": "JSON",
        "error_message": "TEXT",
        "token_estimate": "INTEGER DEFAULT 0",
    },
}


def ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    preparer = IdentifierPreparer(engine.dialect)
    with engine.begin() as connection:
        for table_name, patch_columns in SCHEMA_PATCHES.items():
            if table_name not in inspector.get_table_names():
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_sql in patch_columns.items():
                if column_name in existing:
                    continue
                table_sql = preparer.quote(table_name)
                column_name_sql = preparer.quote(column_name)
                connection.execute(text(" ".join(["ALTER TABLE", table_sql, "ADD COLUMN", column_name_sql, column_sql])))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
