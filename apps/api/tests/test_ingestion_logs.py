from __future__ import annotations


def test_ingestion_logs_are_persisted_and_replayed(db_session, sample_course):
    from app.models import IngestionBatch
    from app.services.ingestion_logs import emit_ingestion_log, list_ingestion_logs, subscribe_ingestion_logs, unsubscribe_ingestion_logs

    batch = IngestionBatch(course_id=sample_course.id, source_root="unit-tests", trigger_source="upload", status="queued")
    db_session.add(batch)
    db_session.commit()
    db_session.refresh(batch)

    emit_ingestion_log(batch.id, "batch_started", "Parsing test file", processed_files=0, total_files=1)
    emit_ingestion_log(batch.id, "batch_completed", "Batch completed", processed_files=1, total_files=1)

    persisted = list_ingestion_logs(batch.id)
    assert [item["event"] for item in persisted] == ["batch_started", "batch_completed"]
    assert persisted[0]["processed_files"] == 0
    assert persisted[0]["log_id"]

    history, subscriber = subscribe_ingestion_logs(batch.id)
    try:
        assert [item["event"] for item in history] == ["batch_started", "batch_completed"]
    finally:
        unsubscribe_ingestion_logs(batch.id, subscriber)
