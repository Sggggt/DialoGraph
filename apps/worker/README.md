# Worker

Background processing for:

- Celery ingestion tasks
- filesystem watching for source course folders

## Run

```bash
uv sync
uv run celery -A worker_app.celery_app worker --loglevel=info
uv run python -m worker_app.watcher
```

