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
        ROUTES --> RETRIEVAL["Retrieval Pipeline<br/>Dense recall + BM25 recall<br/>WSF when both channels hit + lightweight_rerank"]
        ROUTES --> AGENT["Agent QA<br/>analyze / route / rewrite / retrieve / grade / answer / trace"]
        ROUTES --> GRAPH["Knowledge Graph<br/>concepts / relations / chapters / node detail"]
    end

    subgraph INFRA["可复用基础设施"]
        POSTGRES[("postgres:16<br/>metadata, chunks, graph, QA sessions")]
        REDIS[("redis:7<br/>broker / runtime cache")]
        QDRANT[("qdrant/qdrant:v1.13.2<br/>knowledge_chunks vectors")]
    end

    subgraph HOST["宿主机挂载目录"]
        DATA["data/<br/>course files, ingestion artifacts, DB data"]
        BRIDGE["可选 Windows model bridge<br/>127.0.0.1:${MODEL_BRIDGE_PORT}<br/>curl.exe 转发"]
    end

    subgraph MODEL["外部模型 API"]
        LLM["OpenAI-compatible endpoint<br/>embeddings / chat / graph extraction"]
    end

    API --> POSTGRES
    API --> REDIS
    API --> QDRANT
    API -->|"MODEL_BRIDGE_ENABLED=false<br/>real API, fallback disabled"| LLM
    API -. "MODEL_BRIDGE_ENABLED=true<br/>http://host.docker.internal:${MODEL_BRIDGE_PORT}" .-> BRIDGE
    BRIDGE -. "Windows curl.exe<br/>forwards to OPENAI_BASE_URL" .-> LLM

    POSTGRES --- DATA
    REDIS --- DATA
    QDRANT --- DATA
    API --- DATA
```

API 镜像保持轻量，不包含 PyTorch、CUDA 或 `sentence-transformers`。检索使用纯 Python 的 `lightweight_rerank()` 做精排：基于 query terms 与文档的 term overlap 密度，结合 WSF fused score 加权重排序，零外部模型依赖。模型 API 有两种互斥访问路径：默认由 API 容器直连 `OPENAI_BASE_URL`；Windows Docker Desktop 下如果宿主机能访问供应商但 Linux 容器无法完成 TLS 握手，可以设置 `MODEL_BRIDGE_ENABLED=true`，启动宿主机 model bridge，并让 API 容器访问 `http://host.docker.internal:${MODEL_BRIDGE_PORT}`。bridge 只转发到真实 OpenAI-compatible provider，不替换模型，也不是 fallback。

## 目录结构

```text
apps/api/             FastAPI 后端
apps/web/             Next.js 前端
apps/worker/          可选后台 worker
packages/shared/      前后端共享 TypeScript 契约
infra/                Docker Compose 基础设施编排
data/                 本地持久化数据
```

运行时主要持久化目录：

```text
data/postgres         PostgreSQL 数据
data/qdrant           Qdrant 数据
data/redis            Redis 数据
data/storage          上传和归档文件
data/ingestion        解析产物
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
        DENSE --> WSF["Weighted Score Fusion<br/>when BM25 also hits"]
        BM25 --> WSF
        DENSE --> DENSE_ONLY["Dense-only candidates<br/>when lexical channel has no hit"]
        WSF --> TOPK["Top-K chunks"]
        TOPK --> ANSWER["Chat model answer<br/>with citations"]
    end

    VEC --> DENSE
    DBW --> BM25
    KG -. "multi-hop graph expansion" .-> ONLINE
```

当前主检索路径：

```text
Dense 向量召回 + BM25 词面召回；两路同时命中时执行 WSF 融合；仅向量命中时跳过 WSF；最后执行 lightweight_rerank 轻量精排。
```

## GraphRAG 工作流

图谱构建、图谱浏览和关系存储已经可用。当前 `multi_hop_research` 路由会使用 `graph_enhanced_search`：先运行混合检索，再根据命中 chunk 的 `evidence_chunk_id` 找到相关概念和 1-hop 关系，把关系证据 chunk 作为候选补充，并用 `graph_boost` 合并排序。语义门控和 relation-evidence 支持性验证仍是待升级能力。

```mermaid
flowchart LR
    BASE["Hybrid retrieval top chunks"] --> SEED["Find seed relations<br/>by evidence_chunk_id"]
    SEED --> HOP["1-hop relation expansion"]
    HOP --> EVIDENCE["Related evidence chunks"]
    EVIDENCE --> BOOST["graph_boost merge<br/>with base candidates"]
    BOOST --> FINAL["Final top-K context"]
    EVIDENCE -. "planned semantic gate<br/>query-relation-evidence support" .-> BOOST
```

升级原则：

- 图谱扩展只能补充候选 evidence，不能绕过文本证据直接提升答案。
- 关系必须由 evidence chunk 支持；后续升级会增加 relation type 与查询意图的语义校验。
- comparison / relationship 类问题会进入多跳检索路径；普通定义类问题默认走主检索路径。

## 智能体问答流程

```mermaid
flowchart TB
    Q["User question"] --> ANALYZE["query_analyzer"]
    ANALYZE --> ROUTER["router"]
    ROUTER -->|"direct / clarify"| TEMPLATE["template answer<br/>no chat model call"]
    ROUTER -->|"retrieve"| REWRITE["query_rewriter"]
    ROUTER -->|"multi-hop / relation"| REWRITE
    REWRITE --> SPLIT["split_multi_hop_query<br/>only for multi_hop_research"]
    SPLIT --> RETRIEVE["retrievers<br/>hybrid_search_chunks or graph_enhanced_search"]
    RETRIEVE --> GRADE["document_grader"]
    GRADE -->|"insufficient, retry available"| RETRY["retry_planner"]
    RETRY --> REWRITE
    GRADE -->|"has evidence"| CONTEXT["context_synthesizer"]
    CONTEXT --> ANSWER["answer_generator<br/>ChatProvider answer"]
    TEMPLATE --> CITE["citation_checker"]
    ANSWER --> CITE["citation_checker"]
    CITE --> SELF["self_check"]
    SELF --> FINAL["final response + trace"]
```

Agent 运行信息写入 `agent_runs`，节点事件写入 `agent_trace_events`。`/api/tasks/{run_id}` 和 `/api/agent/runs/{run_id}` 用于查询运行状态。回答响应包含 `answer_model_audit`：课程检索回答会记录 `provider`、`model` 和 `external_called=true`；`direct_answer` / `clarify` 模板分支会记录 `skipped_reason`，明确说明没有调用 chat model。

## 配置

创建环境文件：

```powershell
Copy-Item .env.example .env
```

关键配置：

```env
API_IMAGE=course-kg-api:local
WEB_IMAGE=course-kg-web:local

DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/course_kg
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0

OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_RESOLVE_IP=
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus

MODEL_BRIDGE_ENABLED=false
MODEL_BRIDGE_PORT=8765

ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

上面的默认示例按 DashScope 的 OpenAI-compatible 接口配置，因此 `OPENAI_BASE_URL`、`EMBEDDING_MODEL`、`CHAT_MODEL` 和 `EMBEDDING_DIMENSIONS` 是一组。使用其他 OpenAI-compatible 供应商时，必须同时替换这几项，确保 embedding 模型输出维度与 `EMBEDDING_DIMENSIONS` 一致。`OPENAI_API_KEY` 留空时，Docker 服务可以启动，但上传解析、检索和问答里的真实模型调用会失败；完整链路验收前必须填入真实 key。不要开启 `ENABLE_MODEL_FALLBACK` 或 `ENABLE_DATABASE_FALLBACK`。

前端设置页可以查看运行时状态。Web 会调用 `/api/settings/runtime-check` 检查 `.env` / `.env.example` key 同步、PostgreSQL、Qdrant 与 Redis 连通性；如果基础设施不完整，会弹出结构化错误窗口并给出修复命令，不会静默保存失败配置。

`MODEL_BRIDGE_ENABLED=true` 只用于 Windows Docker Desktop 中“宿主机能访问 OpenAI-compatible 供应商，但 Linux 容器无法完成供应商 TLS 握手”的情况。启动脚本会在宿主机启动 `infra/model-bridge/model_bridge.py`，并把 API 容器指向 `http://host.docker.internal:${MODEL_BRIDGE_PORT}`。bridge 会继续转发到 `OPENAI_BASE_URL`，可配合 `OPENAI_RESOLVE_IP` 使用；它不替换模型，也不启用 fallback。

## 拉取基础镜像

基础设施镜像直接从 Docker Hub 拉取，无需本地构建：

```powershell
docker pull postgres:16
docker pull redis:7
docker pull qdrant/qdrant:v1.13.2
```

## 验证基础镜像

确认镜像已正确下载：

```powershell
docker run --rm postgres:16 postgres --version
docker run --rm redis:7 redis-server --version
docker image inspect qdrant/qdrant:v1.13.2
```

项目应用镜像（API、Web）按下一节构建。

## 构建镜像

只构建缺少或需要更新的镜像：

```powershell
docker build -f apps/api/Dockerfile -t course-kg-api:local .
docker build -f apps/web/Dockerfile -t course-kg-web:local .
```

新机器至少需要构建 API 和 Web 两个镜像。

## 系统 Python 测试

API 单测可以走系统 Python。首次运行前在 `apps/api` 下同步依赖：

```powershell
cd apps/api
python -m pip install -e ".[dev]"
python -m pytest
```

这条路径用于本机单测；Docker 运行态诊断仍以容器内环境为准。



## 启动

启动完整应用：

```powershell
.\start-app.ps1
```

启动脚本会读取仓库根目录的 `.env`，并把 API 容器内的数据库、Qdrant、Redis 地址改写为 Docker 网络内地址。不要直接把 README 里的 `localhost` 数据库地址写进 compose；compose 已经负责在容器内使用 `postgres`、`qdrant`、`redis` 服务名。

常用参数：

```powershell
.\start-app.ps1 -NoBrowser
.\start-app.ps1 -BackendPort 8001 -FrontendPort 3001 -OpenPath "/search"
```

停止服务：

```powershell
docker compose -f infra/docker-compose.yml down
```

## Docker 全链路 Smoke Test

启动后可以用一次性课程验证 API、Web 依赖服务、数据库、Qdrant 与真实模型调用：

```powershell
python scripts/docker_smoke.py --base-url http://127.0.0.1:8000/api
```

脚本会创建临时课程、上传一份 Markdown、触发解析、检索、问答、读取会话并清理课程。`/api/search` 返回的 `model_audit.embedding_external_called=true` 且 `embedding_fallback_reason=null` 才表示模型调用链路没有走 fallback。

只检查 Docker 基础设施连通性时：

```powershell
python scripts/docker_smoke.py --skip-model-calls
```

## API 端点

FastAPI 的 OpenAPI schema 暴露在 `/openapi.json`。下表按当前 schema 和前端调用链路整理。

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/health` | 健康检查与 degraded 状态 |
| GET | `/api/settings/model` | 读取模型配置 |
| PUT | `/api/settings/model` | 更新模型配置 |
| GET | `/api/settings/runtime-check` | 检查 `.env` 同步与基础设施状态 |
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
| POST | `/api/search` | 混合检索，返回 `model_audit` 说明查询 embedding 是否真实外呼 |
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
- API 容器只承载项目后端依赖，保持轻量。
- 生产数据应使用 PostgreSQL + Qdrant，不依赖 SQLite 或 JSON fallback。
- API 启动时会执行轻量 schema patch 和中断导入批次收敛，不使用 Alembic 迁移。
- 课程数据按课程隔离，上传文件、解析产物、chunks、图谱、QA 会话都绑定 `course_id`。
- `retrieval_architecture.md` 属于本地设计草稿，当前 README 是项目架构说明的主要入口。
