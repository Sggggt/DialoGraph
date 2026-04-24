# API

FastAPI service for:

- file upload and file tracking
- document parsing and chunking
- vector retrieval
- citation-grounded question answering
- concept cards and graph queries

## Run

```bash
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

## Environment

Copy `.env.example` to `.env` and adjust values.

