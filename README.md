# DialoGraph

DialoGraph 是一个本地课程知识库系统。它把 PDF、PPT/PPTX、DOCX、Markdown、TXT、Notebook、HTML 和图片资料解析成可检索的文本块、向量索引、概念卡片、知识图谱关系和带引用的问答会话。

系统已经支持多课程隔离：每门课程有独立的文件目录、导入批次、图谱、检索结果和问答历史。

## Architecture

```mermaid
flowchart TB
    U["User Browser"] --> W["Next.js Web<br/>apps/web"]
    W -->|HTTP / SSE| API["FastAPI API<br/>apps/api"]

    subgraph API_RUNTIME["API Runtime"]
        API --> ROUTES["API Routes<br/>courses / files / search / qa / graph"]
        ROUTES --> INGEST["Ingestion Service<br/>parse -> chunk -> embed -> graph"]
        ROUTES --> RETRIEVAL["Retrieval Service<br/>dense + lexical + RRF"]
        ROUTES --> AGENT["LangGraph Agent<br/>rewrite / route / grade / answer / self-check"]
        INGEST --> PARSERS["Document Parsers<br/>PDF / PPTX / DOCX / Markdown / Notebook / OCR"]
        INGEST --> GRAPH["Concept Graph Builder<br/>concepts + relations"]
    end

    subgraph WORKERS["Optional Background Components"]
        CELERY["Celery Worker<br/>apps/worker"] --> INGEST
        WATCHER["Watchdog File Watcher"] --> CELERY
    end

    subgraph STORES["Storage Layer"]
        DB[("PostgreSQL<br/>primary metadata store")]
        SQLITE[("SQLite fallback<br/>development / local recovery")]
        QDRANT[("Qdrant<br/>vector collection: knowledge_chunks")]
        FS["Local data root<br/>data/<Course Name>/"]
        REDIS[("Redis<br/>Celery broker")]
    end

    API --> DB
    API -. "database fallback" .-> SQLITE
    API --> QDRANT
    API --> FS
    CELERY --> REDIS

    subgraph MODEL["Model Provider"]
        LLM["OpenAI-compatible endpoint<br/>DashScope / OpenAI / compatible APIs"]
    end

    API -->|embeddings / chat / graph extraction| LLM
```

## Components

- `apps/web`: Next.js 前端工作台，包含上传、搜索、问答、图谱、概念卡片和设置页面。
- `apps/api`: FastAPI 后端，负责课程管理、文件导入、解析、切块、embedding、检索、知识图谱构建和 Agent 问答。
- `apps/worker`: 可选后台组件，包含 Celery ingestion worker 和目录监听器。
- `packages/shared`: 前后端共享的 TypeScript 数据契约。
- `infra`: 本地基础设施，包含 PostgreSQL、Redis、Qdrant 的 Docker Compose 配置。

## Fallback Policy

默认 fallback 是上锁的：

```env
ENABLE_MODEL_FALLBACK=false
```

这意味着系统不会静默降级到假 embedding、抽取式答案或本地 JSON 向量索引。默认要求：

- `OPENAI_API_KEY` 可用，用于 embedding、chat 和图谱抽取。
- `QDRANT_URL` 指向可访问的 Qdrant。
- `DATABASE_URL` 指向可访问的 PostgreSQL。

只有在本地离线调试时才建议显式解锁：

```env
ENABLE_MODEL_FALLBACK=true
```

解锁后，系统可能使用 deterministic local hash embedding、extractive fallback answer，或 `data/<Course Name>/ingestion/vector_index.json` 作为向量索引兜底。这些结果只适合开发验证，不适合作为正式知识库质量判断。

数据库层还有一个开发用 SQLite fallback：如果 PostgreSQL 不可用，或者 PostgreSQL 是空库而本地 SQLite 已有数据，API 会尝试使用 `apps/course_kg.db` 或 `apps/knowledge_base.db`。生产环境不要依赖这个行为。

## Data Layout

默认数据根目录是 `data/`。每门课程会创建独立目录：

```text
data/
  <Course Name>/
    storage/       uploaded files and archived copies
    ingestion/     extracted JSON and optional fallback vector_index.json
    source/        optional watched source files
  qdrant/          Qdrant persistent storage
  postgres/        PostgreSQL persistent storage
  redis/           Redis persistent storage
```

主要持久化位置：

- 图谱节点和关系：PostgreSQL 表 `concepts`、`concept_relations`。
- 问答历史：PostgreSQL 表 `qa_sessions`，消息在 `transcript` 字段。
- Agent 运行轨迹：`agent_runs`、`agent_trace_events`。
- 文档、版本、切块和导入批次：`documents`、`document_versions`、`chunks`、`ingestion_batches`、`ingestion_jobs`。
- 向量索引：Qdrant collection `knowledge_chunks`。

## Prerequisites

- Node.js `>= 20.9.0`
- Python `>= 3.11`
- `uv` for Python dependency management
- Docker Desktop or Docker Engine with Compose v2

Install `uv` if needed:

```powershell
python -m pip install uv
```

## Configuration

Create the root environment file:

```powershell
Copy-Item .env.example .env
```

Minimum local development configuration:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/knowledge_base
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=knowledge_chunks
REDIS_URL=redis://localhost:6379/0
COURSE_NAME=Sample Course
DATA_ROOT=./data
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus
EMBEDDING_DIMENSIONS=1024
ENABLE_MODEL_FALLBACK=false
```

If you use DashScope or another OpenAI-compatible endpoint, set `OPENAI_BASE_URL` and `OPENAI_API_KEY` accordingly.

## Install Dependencies

Install frontend workspace dependencies from the repo root:

```powershell
npm install
```

Install API dependencies:

```powershell
cd apps/api
uv sync
```

Install worker dependencies if you need background ingestion:

```powershell
cd apps/worker
uv sync
```

## Build and Start Backend Infrastructure

The current repository has Docker Compose for backend infrastructure only: PostgreSQL, Redis and Qdrant. It does not currently include Dockerfiles for API/Web/Worker application images.

Pull the infrastructure images:

```powershell
docker compose -f infra/docker-compose.yml pull
```

Start the backend infrastructure:

```powershell
docker compose -f infra/docker-compose.yml up -d
```

Check status:

```powershell
docker compose -f infra/docker-compose.yml ps
```

Recreate containers after image updates:

```powershell
docker compose -f infra/docker-compose.yml pull
docker compose -f infra/docker-compose.yml up -d --force-recreate
```

`docker compose build` is not useful for the current infra file because all three services use public images directly (`postgres:16`, `redis:7`, `qdrant/qdrant:v1.13.2`) and no local `build:` context is defined.

## Start Application Services

Recommended Windows launcher from the repo root:

```powershell
.\start-app.ps1
```

The launcher starts:

- API on `http://127.0.0.1:8000`
- Web on `http://127.0.0.1:3000`
- Browser path defaults to `/graph`

Run without opening a browser:

```powershell
.\start-app.ps1 -NoBrowser
```

Use custom ports:

```powershell
.\start-app.ps1 -BackendPort 8001 -FrontendPort 3001 -OpenPath "/search"
```

Manual API start:

```powershell
cd apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Manual Web start:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000/api"
npm run dev --workspace web -- --hostname 127.0.0.1 --port 3000
```

Optional worker:

```powershell
cd apps/worker
uv run celery -A worker_app.celery_app worker --loglevel=info
```

Optional watched-folder ingestion:

```powershell
cd apps/worker
uv run python -m worker_app.watcher
```

## Build Frontend

Type-check and build the web app:

```powershell
npm run typecheck:web
npm run build:web
```

Start the production Next.js server after build:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000/api"
npm run start --workspace web
```

## Ingestion Flow

```mermaid
sequenceDiagram
    participant User
    participant Web
    participant API
    participant FS as Course Storage
    participant DB as PostgreSQL
    participant Model as Model API
    participant Qdrant

    User->>Web: Upload or sync course files
    Web->>API: POST /api/files/upload or /api/ingestion/sync-source
    API->>FS: Save archived copy
    API->>API: Parse document
    API->>API: Chunk sections
    API->>Model: Create embeddings
    API->>Qdrant: Upsert vectors
    API->>DB: Store documents, versions, chunks
    API->>Model: Extract concept graph payload
    API->>DB: Store concepts and relations
    API-->>Web: Batch status and graph/search readiness
```

## Main API Endpoints

- `GET /api/courses`
- `POST /api/courses`
- `GET /api/courses/current/dashboard?course_id=...`
- `GET /api/courses/current/graph?course_id=...`
- `GET /api/graph/chapters/{chapter}?course_id=...`
- `GET /api/graph/nodes/{concept_id}?course_id=...`
- `GET /api/concepts?course_id=...`
- `POST /api/files/upload?course_id=...`
- `POST /api/ingestion/sync-source?course_id=...`
- `POST /api/search`
- `POST /api/qa`
- `POST /api/qa/stream`
- `GET /api/sessions?course_id=...`

## Development Notes

- Keep `.env`, `data/`, local databases and generated logs out of Git.
- `ingestion/` contains derived extraction artifacts; it can be regenerated from stored source documents.
- `storage/` contains uploaded or copied source files; deleting it removes the material needed for re-ingestion.
- The API uses lightweight schema patching at startup instead of Alembic migrations.
- Authentication and production-grade authorization are not implemented yet.
