[English](./README.en.md) | **中文**

# DialoGraph

DialoGraph 是一个 Docker-first 的本地课程知识库系统。它将 PDF、PPT/PPTX、DOCX、Markdown、TXT、Notebook、HTML 和图片资料解析为可检索的文本块、向量索引、概念图谱和带引用的问答结果。

系统默认使用真实 PostgreSQL、Qdrant、Redis 和 OpenAI-compatible 模型 API。模型 fallback 与数据库 fallback 默认关闭。

## 系统架构

```mermaid
flowchart TB
    U["用户浏览器"] --> WEB["Next.js Web<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["FastAPI API<br/>course-kg-api"]

    subgraph APP["项目应用镜像"]
        WEB
        API --> ROUTES["API Routes<br/>courses / files / ingestion / search / qa / graph / settings"]
        ROUTES --> INGEST["Ingestion Pipeline<br/>parse -> chunk -> embed -> vector upsert -> graph extraction"]
        ROUTES --> RETRIEVAL["Retrieval Pipeline<br/>Dense recall + BM25 recall + WSF fusion + rerank"]
        ROUTES --> AGENT["Agent QA<br/>rewrite / route / retrieve / grade / answer / trace"]
        ROUTES --> GRAPH["Knowledge Graph<br/>concepts / relations / chapters / node detail"]
    end

    subgraph INFRA["可复用基础设施"]
        POSTGRES[("postgres:16<br/>metadata, chunks, graph, QA sessions")]
        REDIS[("redis:7<br/>broker / runtime cache")]
        QDRANT[("qdrant/qdrant:v1.13.2<br/>knowledge_chunks vectors")]
        RERANK_CPU["text-reranker-runtime:cpu<br/>CrossEncoder rerank"]
        RERANK_CUDA["text-reranker-runtime:cuda<br/>CrossEncoder rerank + GPU"]
    end

    subgraph HOST["宿主机挂载目录"]
        DATA["data/<br/>course files, ingestion artifacts, DB data"]
        MODELS["models/<br/>Hugging Face cache"]
    end

    subgraph MODEL["外部模型 API"]
        LLM["OpenAI-compatible endpoint<br/>embeddings / chat / graph extraction"]
    end

    API --> POSTGRES
    API --> REDIS
    API --> QDRANT
    API -->|"HTTP rerank via RERANKER_URL"| RERANK_CPU
    API -. "RERANKER_DEVICE=cuda" .-> RERANK_CUDA
    API -->|"real API, fallback disabled"| LLM

    POSTGRES --- DATA
    REDIS --- DATA
    QDRANT --- DATA
    API --- DATA
    RERANK_CPU --- MODELS
    RERANK_CUDA --- MODELS
```

API 镜像保持轻量，不包含 PyTorch、CUDA 或 `sentence-transformers`。重排模型运行在独立的通用 `text-reranker-runtime:*` 容器中；`RERANKER_ENABLED=true` 时启用，CPU/CUDA 由 `.env` 的 `RERANKER_DEVICE` 控制。

## 目录结构

```text
apps/api/             FastAPI 后端
apps/web/             Next.js 前端
apps/worker/          可选后台 worker
packages/shared/      前后端共享 TypeScript 契约
infra/                Docker Compose 与通用 reranker runtime
data/                 本地持久化数据
models/               本地模型缓存
```

运行时主要持久化目录：

```text
data/postgres         PostgreSQL 数据
data/qdrant           Qdrant 数据
data/redis            Redis 数据
data/storage          上传和归档文件
data/ingestion        解析产物
models/huggingface    reranker 模型缓存
```

## 数据模型架构

```mermaid
erDiagram
    Course ||--o{ Document : has
    Course ||--o{ Concept : has
    Course ||--o{ IngestionBatch : has
    Course ||--o{ IngestionJob : has
    Course ||--o{ QASession : has
    Course ||--o{ AgentRun : has
    Document ||--o{ DocumentVersion : versions
    Document ||--o{ Chunk : chunks
    DocumentVersion ||--o{ Chunk : chunks
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source
    Concept ||--o{ ConceptRelation : target
    IngestionBatch ||--o{ IngestionJob : jobs
    IngestionBatch ||--o{ IngestionLog : logs
    IngestionJob ||--o{ IngestionCompensationLog : compensations
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

核心表：

- `courses`：课程工作区，课程名唯一。
- `documents` / `document_versions`：文档与版本，支持 inactive 新版本到 active 版本的两阶段切换。
- `chunks`：可检索文本块，保存原始 chunk 内容、摘要、章节、页码、来源类型和 embedding 状态。
- `concepts` / `concept_aliases` / `concept_relations`：概念、别名和图谱关系；关系可关联 `evidence_chunk_id`。
- `ingestion_batches` / `ingestion_jobs` / `ingestion_logs` / `ingestion_compensation_logs`：导入批次、单文件任务、SSE 日志和向量补偿记录。
- `qa_sessions` / `agent_runs` / `agent_trace_events`：问答会话、Agent 运行记录和节点级 trace。

## 并发与异步模型

```mermaid
flowchart LR
    REQ["FastAPI request"] --> ASYNC["async route handlers"]
    ASYNC --> BG["BackgroundTasks<br/>batch ingestion"]
    ASYNC --> LLM["async model calls<br/>httpx / thread offload"]
    BG --> LOCKS["source_path locks<br/>batch mutex"]
    BG --> DBTX["SQLAlchemy transaction"]
    BG --> VECTOR["Qdrant upsert"]
    BG --> LOGS["SSE ingestion logs"]
    AGENT["Agent graph"] --> NODE["node execution"]
    NODE --> TRACE["commit current_node + trace event"]
```

并发控制：

- SQLAlchemy 会话使用显式事务，导入失败时回滚当前文件或当前批次的受影响部分。
- 同一课程同一时间只保留一个非终态导入批次，避免重复解析和重复写向量。
- 同一文件通过 `source_path` 应用层锁串行化导入。
- 图谱抽取使用有限并发，避免模型 API 过载。
- Agent 每个节点都会提交 `current_node` 和 `agent_trace_events`，前端可实时轮询或流式展示进度。
- Qdrant 写入失败时通过补偿日志记录待恢复操作，应用启动时会执行中断批次收敛和补偿处理。

## 降级策略

默认配置：

```env
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

默认行为：

- 模型 API 不可用时直接失败，不静默切换到假 embedding 或抽取式回答。
- PostgreSQL 不可用时直接失败，不静默切换到 SQLite。
- Qdrant 不可用时检索失败，不把本地 JSON fallback 当作生产检索路径。
- `/api/health` 会返回 `degraded_mode`，评估和正式运行应要求其为 `false`。

fallback 仅用于显式的离线开发或兼容性测试，不应用于系统质量评估或生产数据判断。

## 导入流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant Web as Web
    participant API as API
    participant FS as data/storage
    participant DB as PostgreSQL
    participant Model as Model API
    participant Qdrant as Qdrant

    User->>Web: 上传或选择课程文件
    Web->>API: POST /api/files/upload
    API->>FS: 保存归档文件
    API->>DB: 创建 document/job
    Web->>API: POST /api/ingestion/parse-uploaded-files
    API->>DB: 创建 ingestion batch
    API-->>Web: batch_id
    API->>API: 解析 PDF/PPT/DOCX/MD/Notebook/HTML/Image
    API->>API: chunk、去重、过滤低信息块
    API->>Model: 生成 metadata-enriched embedding
    API->>Qdrant: upsert vectors
    API->>DB: 激活 document version 和 chunks
    API->>Model: 抽取概念与关系
    API->>DB: 单事务重建图谱
    API-->>Web: SSE 批次日志与最终状态
```

导入写入策略：

- 文档解析产物写入 `data/ingestion`。
- 上传和归档文件写入 `data/storage`。
- chunk 原文进入 PostgreSQL，embedding 文本会额外拼接文档、章节、section、来源类型等元数据。
- 向量写入 Qdrant 后再激活新版本，降低 DB 与向量库不一致的概率。
- 图谱关系写入 PostgreSQL，关系 evidence 指向真实 chunk。

## RAG 架构

```mermaid
flowchart TB
    subgraph OFFLINE["离线索引阶段"]
        SRC["Course files"] --> PARSE["Document parsers"]
        PARSE --> CHUNK["Semantic chunking<br/>dedup + filtering"]
        CHUNK --> EMBTXT["Metadata-enriched embedding text"]
        EMBTXT --> EMB["Embedding API"]
        EMB --> VEC[("Qdrant knowledge_chunks")]
        CHUNK --> DBW[("PostgreSQL chunks")]
        CHUNK --> GRAPH_EXTRACT["LLM graph extraction"]
        GRAPH_EXTRACT --> KG[("Concepts + Relations")]
    end

    subgraph ONLINE["在线查询阶段"]
        Q["User query"] --> QT["Query type classifier"]
        QT --> DENSE["Dense vector recall"]
        QT --> BM25["BM25 lexical recall"]
        DENSE --> WSF["Weighted Score Fusion<br/>query-type alpha + min-max"]
        BM25 --> WSF
        WSF --> RERANK["Optional external bge-reranker<br/>HTTP runtime"]
        RERANK --> TOPK["Top-K chunks"]
        TOPK --> ANSWER["Chat model answer<br/>with citations"]
    end

    VEC --> DENSE
    DBW --> BM25
    KG -. "pending controlled GraphRAG" .-> ONLINE
```

当前主检索路径：

```text
Dense 向量召回 + BM25 词面召回 + WSF 融合 + 可选外部 bge-reranker 重排
```

## GraphRAG 工作流

图谱构建、图谱浏览和关系存储已经可用；GraphRAG 增强检索仍是待升级能力，进入主排序链路前需要完成语义门控和 evidence 支持性验证。

```mermaid
flowchart LR
    BASE["Hybrid retrieval top chunks"] --> SEED["Find concepts and relations<br/>from evidence chunks"]
    SEED --> HOP["1-hop relation expansion"]
    HOP --> EVIDENCE["Candidate evidence chunks"]
    EVIDENCE --> GATE["Semantic gate<br/>query-relation-evidence support"]
    GATE --> MERGE["Controlled merge"]
    MERGE --> RERANK["Rerank with base candidates"]
    RERANK --> FINAL["Final top-K context"]
```

升级原则：

- 图谱扩展只能补充候选 evidence，不能绕过文本证据直接提升答案。
- 关系必须由 evidence chunk 支持，且 relation type 要通过语义校验。
- comparison / relationship 类问题优先启用图谱门控；普通定义类问题默认走主检索路径。

## 智能体问答流程

```mermaid
flowchart TB
    Q["User question"] --> ANALYZE["query_analyzer"]
    ANALYZE --> ROUTER["router"]
    ROUTER -->|"direct / clarify"| ANSWER["answer_generator"]
    ROUTER -->|"retrieve"| REWRITE["query_rewriter"]
    ROUTER -->|"multi-hop / relation"| MULTIHOP["multi_hop_rewriter"]
    REWRITE --> RETRIEVE["hybrid_retriever<br/>Dense + BM25 + WSF + optional rerank"]
    MULTIHOP --> RETRIEVE
    RETRIEVE --> GRADE["document_grader"]
    GRADE -->|"insufficient, retry available"| RETRY["retry_planner"]
    RETRY --> REWRITE
    GRADE -->|"has evidence"| CONTEXT["context_synthesizer"]
    CONTEXT --> ANSWER
    ANSWER --> CITE["citation_checker"]
    CITE --> SELF["self_check"]
    SELF --> FINAL["final response + trace"]
```

Agent 运行信息写入 `agent_runs`，节点事件写入 `agent_trace_events`。`/api/tasks/{run_id}` 和 `/api/agent/runs/{run_id}` 用于查询运行状态。

## 配置

创建环境文件：

```powershell
Copy-Item .env.example .env
```

关键配置：

```env
API_IMAGE=course-kg-api:local
WEB_IMAGE=course-kg-web:local
RERANKER_CPU_IMAGE=text-reranker-runtime:cpu
RERANKER_CUDA_IMAGE=text-reranker-runtime:cuda

DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/knowledge_base
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus

RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_ENABLED=true
RERANKER_DEVICE=cpu

ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

`RERANKER_ENABLED=false` 时，检索链路直接输出 WSF 融合结果，不启动 reranker runtime；这不是 fallback。`RERANKER_DEVICE` 只支持 `cpu` 和 `cuda`，仅在启用 reranker 时生效。

前端设置页可以直接切换 reranker。启用前，Web 会调用 `/api/settings/runtime-check` 检查 `.env` / `.env.example` key 同步、reranker runtime、模型缓存、PostgreSQL、Qdrant 与 Redis 连通性；如果基础设施不完整，会弹出结构化错误窗口并给出修复命令，不会静默保存失败配置。

## 验证已有基础设施

```powershell
docker run --rm postgres:16 postgres --version
docker run --rm redis:7 redis-server --version
docker image inspect qdrant/qdrant:v1.13.2
docker run --rm text-reranker-runtime:cpu python -c "import torch; print(torch.__version__)"
docker run --rm --gpus all text-reranker-runtime:cuda python -c "import torch; print(torch.cuda.is_available())"
```

CUDA 验证应输出 `True`。如果不是，先检查 NVIDIA Driver 和 NVIDIA Container Toolkit。

## 构建镜像

只构建缺少或需要更新的镜像：

```powershell
docker build -f apps/api/Dockerfile -t course-kg-api:local .
docker build -f apps/web/Dockerfile -t course-kg-web:local .
docker build -f infra/reranker/Dockerfile.cpu -t text-reranker-runtime:cpu infra/reranker
docker build -f infra/reranker/Dockerfile.cuda -t text-reranker-runtime:cuda infra/reranker
```

`text-reranker-runtime:*` 是通用基础设施镜像，可被其他项目复用。已有等价镜像时可在 `.env` 中设置自己的镜像名。

## 模型缓存

Reranker 模型从宿主机挂载，不打进镜像：

```text
models/huggingface -> /models/huggingface
```

预下载模型：

```powershell
docker run --rm -v "${PWD}\models:/models" -e HF_HOME=/models/huggingface text-reranker-runtime:cpu python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=512, device='cpu')"
```

需要 Hugging Face 镜像源时可追加：

```powershell
-e HF_ENDPOINT=https://hf-mirror.com
```

## 启动

启动完整应用：

```powershell
.\start-app.ps1
```

常用参数：

```powershell
.\start-app.ps1 -NoBrowser
.\start-app.ps1 -BackendPort 8001 -FrontendPort 3001 -OpenPath "/search"
```

停止服务：

```powershell
docker compose -f infra/docker-compose.yml down
docker compose -f infra/docker-compose.yml -f infra/docker-compose.cuda.yml down
```

## API 端点

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查与 degraded 状态 |
| GET | `/api/settings/model` | 读取模型配置 |
| PUT | `/api/settings/model` | 更新模型配置 |
| GET | `/api/settings/runtime-check` | 检查 `.env` 同步、reranker runtime 与基础设施状态 |
| GET | `/api/courses` | 课程列表 |
| POST | `/api/courses` | 创建课程 |
| DELETE | `/api/courses/{course_id}` | 删除课程及其数据 |
| GET | `/api/courses/current/dashboard` | 当前课程仪表盘 |
| POST | `/api/courses/current/refresh` | 刷新当前课程状态 |
| GET | `/api/course-files` | 课程文件列表 |
| DELETE | `/api/course-files` | 删除课程文件 |
| POST | `/api/maintenance/cleanup-stale-data` | 清理陈旧数据 |
| POST | `/api/maintenance/cleanup-stale-graph` | 清理陈旧图谱 |
| GET | `/api/courses/current/graph` | 课程图谱 |
| GET | `/api/graph/chapters/{chapter}` | 章节图谱 |
| GET | `/api/graph/nodes/{concept_id}` | 概念节点详情 |
| GET | `/api/concepts` | 概念卡片 |
| POST | `/api/files/upload` | 上传文件 |
| POST | `/api/ingestion/parse-uploaded-files` | 解析已上传文件 |
| POST | `/api/ingestion/parse-storage` | 解析课程 storage 目录 |
| GET | `/api/ingestion/batches/{batch_id}` | 导入批次状态 |
| GET | `/api/ingestion/batches/{batch_id}/logs` | 导入日志 SSE |
| GET | `/api/jobs/{job_id}` | 单文件任务状态 |
| POST | `/api/search` | 混合检索 |
| POST | `/api/qa` | Agent 问答 |
| POST | `/api/qa/stream` | Agent 问答 SSE |
| POST | `/api/agent` | Agent 调用 |
| GET | `/api/agent/runs/{run_id}` | Agent 运行状态 |
| GET | `/api/tasks/{run_id}` | Agent 运行状态别名 |
| GET | `/api/sessions` | 会话列表 |
| GET | `/api/sessions/{session_id}` | 会话摘要 |
| GET | `/api/sessions/{session_id}/messages` | 会话消息 |
| DELETE | `/api/sessions/{session_id}` | 删除会话 |

## 开发说明

- 默认不使用模型 fallback 或数据库 fallback。
- 不把 `.env`、`data/`、`models/`、`output/` 提交到 Git。
- API 容器只承载项目后端依赖；PyTorch/CUDA 只存在于通用 reranker runtime。
- 生产数据应使用 PostgreSQL + Qdrant，不依赖 SQLite 或 JSON fallback。
- API 启动时会执行轻量 schema patch 和中断导入批次收敛，不使用 Alembic 迁移。
- 课程数据按课程隔离，上传文件、解析产物、chunks、图谱、QA 会话都绑定 `course_id`。
- `retrieval_architecture.md` 属于本地设计草稿，当前 README 是项目架构说明的主要入口。
