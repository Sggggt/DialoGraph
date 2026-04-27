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

## Data Model

系统使用 SQLAlchemy ORM 管理以下核心表：

```mermaid
erDiagram
    Course ||--o{ Document : has
    Course ||--o{ Concept : has
    Course ||--o{ IngestionBatch : has
    Document ||--o{ DocumentVersion : versions
    Document ||--o{ Chunk : chunks
    DocumentVersion ||--o{ Chunk : chunks
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source_relations
    IngestionBatch ||--o{ IngestionJob : jobs
    IngestionBatch ||--o{ IngestionLog : logs
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

关键约束：

- `Course.name` UNIQUE — 课程名唯一
- `Concept(course_id, normalized_name)` UNIQUE — 同课程内概念去重
- `DocumentVersion(document_id, version)` UNIQUE — 文档版本号唯一
- 所有主键使用 `UUID v4`（`String(36)`），分布式友好
- `TimestampMixin` 自动维护 `created_at` / `updated_at`
- Schema 演进通过启动时的 `SCHEMA_PATCHES`（`ALTER TABLE ADD COLUMN`）实现

## Storage Coordination

系统涉及三类存储，各有不同的事务保障：

| 存储 | 技术 | 事务保障 |
|------|------|----------|
| 关系数据库 | SQLAlchemy (PG/SQLite) | `autocommit=False`，完整 ACID |
| 向量索引 | Qdrant / FallbackJSON | 无分布式事务 |
| 文件系统 | 本地磁盘 | 无事务 |

**跨存储一致性策略**：

- **图谱构建**采用单事务模式（DELETE→INSERT→单次 COMMIT），ACID 合规。
- **文档解析**因需要实时进度反馈，使用多次 commit，通过应用层补偿保证最终一致性。
- **检索层**对向量存储返回的结果用 DB 做二次验证（过滤已删除或不活跃的 chunk），防御跨存储不一致。
- **批量操作**的异常处理采用 `rollback()→get()→标记 failed→commit()` 模式，单文件失败不污染批次。
- **进程重启恢复**：`finalize_interrupted_batches()` 在 FastAPI lifespan 启动时将未完成的批次标记为 failed。

## Concurrency & Async Model

```
FastAPI (uvicorn) ─── async API routes
  ├── BackgroundTasks ─── asyncio.run() in thread pool
  │     └── run_uploaded_files_ingestion (async)
  ├── LLM calls ─── httpx.AsyncClient / asyncio.to_thread(curl)
  └── Graph extraction ─── asyncio.gather() + Semaphore(2)
```

**并发控制机制**：

| 机制 | 位置 | 保护范围 |
|------|------|----------|
| SQLAlchemy Session | `autocommit=False` | DB 事务隔离 |
| `asyncio.Semaphore(2)` | `extract_llm_graph_payloads` | LLM API 并发限流 |
| `threading.Lock` | `FallbackVectorStore` | JSON 向量文件读写互斥 |
| `_VECTOR_FILE_LOCKS_GUARD` | 锁注册表守卫 | 锁创建的线程安全 |
| 原子文件写入 | `_write` (temp+replace) | 向量文件写入完整性 |

**设计决策**：Agent 流程中每个 LangGraph 节点执行时都会 `db.commit()` 更新 `current_node` 和 trace 事件。这是有意的可观测性设计，使前端能通过 `/tasks/{run_id}` 实时追踪 Agent 执行进度。

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
    Web->>API: POST /api/files/upload or /api/ingestion/parse-uploaded-files
    API->>FS: Save archived copy
    API->>API: Parse document (PDF/PPTX/DOCX/MD/Notebook/OCR)
    API->>API: Chunk sections (RecursiveCharacterTextSplitter)
    API->>Model: Create embeddings (batch, async)
    API->>Qdrant: Upsert vectors
    API->>DB: Store documents, versions, chunks (commit)
    API->>Model: Extract concept graph payload (Semaphore-throttled)
    API->>DB: Rebuild graph (single-transaction DELETE→INSERT→COMMIT)
    API-->>Web: Batch status via SSE stream
```

## Agent QA Flow

```mermaid
graph LR
    A[query_analyzer] --> B[router]
    B -->|retrieve| C[query_rewriter]
    B -->|direct/clarify| G[answer_generator]
    C --> D[retrievers]
    D --> E[document_grader]
    E -->|has docs| F[context_synthesizer]
    E -->|retry < 2| R[retry_planner]
    R --> D
    F --> G
    G --> H[citation_checker]
    H --> I[self_check]
```

每个节点执行时实时更新 `AgentRun.current_node` 和 `AgentTraceEvent`（commit per node），支持前端进度追踪。

## Main API Endpoints

- `GET /api/courses`
- `POST /api/courses`
- `GET /api/courses/current/dashboard?course_id=...`
- `GET /api/courses/current/graph?course_id=...`
- `GET /api/graph/chapters/{chapter}?course_id=...`
- `GET /api/graph/nodes/{concept_id}?course_id=...`
- `GET /api/concepts?course_id=...`
- `POST /api/files/upload?course_id=...`
- `POST /api/ingestion/parse-uploaded-files`
- `POST /api/ingestion/parse-storage?course_id=...`
- `GET /api/ingestion/batches/{batch_id}`
- `GET /api/ingestion/batches/{batch_id}/logs` (SSE)
- `POST /api/search`
- `POST /api/qa`
- `POST /api/qa/stream` (SSE)
- `POST /api/agent`
- `GET /api/tasks/{run_id}`
- `GET /api/sessions?course_id=...`
- `GET /api/sessions/{session_id}/messages`
- `DELETE /api/sessions/{session_id}`
- `GET /api/settings/model`
- `PUT /api/settings/model`

## Development Notes

- Keep `.env`, `data/`, local databases and generated logs out of Git.
- `ingestion/` contains derived extraction artifacts; it can be regenerated from stored source documents.
- `storage/` contains uploaded or copied source files; deleting it removes the material needed for re-ingestion.
- The API uses lightweight schema patching at startup (`SCHEMA_PATCHES` + `ALTER TABLE ADD COLUMN`) instead of Alembic migrations.
- Authentication and production-grade authorization are not implemented yet.
- The `FallbackVectorStore` uses thread-level locks with atomic temp-file writes; it is safe for single-process deployments but not for multi-worker configurations.
- `finalize_interrupted_batches()` runs at startup to mark incomplete batches as failed, providing crash recovery for the ingestion pipeline.
