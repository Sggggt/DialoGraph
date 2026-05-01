[English](./README.en.md) | **中文**

# DialoGraph

DialoGraph 是一个本地课程知识库系统。它把 PDF、PPT/PPTX、DOCX、Markdown、TXT、Notebook、HTML 和图片资料解析成可检索的文本块、向量索引、概念卡片、知识图谱关系和带引用的问答会话。

系统已经支持多课程隔离：每门课程有独立的文件目录、导入批次、图谱、检索结果和问答历史。

## 系统架构

```mermaid
flowchart TB
    U["用户浏览器"] --> W["Next.js 前端<br/>apps/web"]
    W -->|HTTP / SSE + API Key| SECURITY["认证与跨域中间件<br/>API Key / CORS 白名单"]
    SECURITY --> API["FastAPI 后端<br/>apps/api"]

    subgraph API_RUNTIME["后端运行时"]
        API --> ROUTES["API 路由<br/>课程 / 文件 / 搜索 / 问答 / 图谱"]
        ROUTES --> INGEST["导入服务<br/>解析 → inactive DB 版本 → 向量 upsert → 激活"]
        ROUTES --> RETRIEVAL["检索服务<br/>Dense 向量召回 + BM25 词面召回 + WSF 融合 + bge-reranker 重排"]
        ROUTES --> AGENT["LangGraph 智能体<br/>改写 / 路由 / 评分 / 回答 / 实时轨迹"]
        INGEST --> PARSERS["文档解析器<br/>PDF / PPTX / DOCX / Markdown / Notebook / OCR"]
        INGEST --> GRAPH["概念图谱构建器<br/>概念 + 关系"]
        INGEST --> COMP["补偿恢复<br/>IngestionCompensationLog"]
        AGENT --> TRACE["SSE 轨迹发布<br/>agent_trace_events"]
    end

    subgraph WORKERS["可选后台组件"]
        CELERY["Celery Worker<br/>apps/worker"] --> INGEST
        WATCHER["目录监听器"] --> CELERY
    end

    subgraph STORES["存储层"]
        DB[("PostgreSQL<br/>主元数据存储")]
        SQLITE[("SQLite 降级<br/>仅显式兼容路径")]
        QDRANT[("Qdrant<br/>默认向量存储: knowledge_chunks")]
        FALLBACK[("Fallback JSON 向量索引<br/>仅 fallback_compat 测试")]
        FS["本地数据目录<br/>data/<课程名>/"]
        REDIS[("Redis<br/>Celery 消息代理")]
    end

    API --> DB
    API -. "ENABLE_DATABASE_FALLBACK=true" .-> SQLITE
    API --> QDRANT
    COMP --> QDRANT
    API -. "ENABLE_MODEL_FALLBACK=true" .-> FALLBACK
    API --> FS
    CELERY --> REDIS

    subgraph MODEL["模型提供方"]
        LLM["OpenAI 兼容端点<br/>DashScope / OpenAI / 兼容 API"]
    end

    subgraph LOCAL_MODEL["本地模型缓存"]
        RERANKER["Cross-Encoder reranker<br/>BAAI/bge-reranker-v2-m3"]
    end

    API -->|向量化 / 对话 / 图谱抽取| LLM
    RETRIEVAL -->|top-N 重排| RERANKER
```

## 项目组成

- `apps/web`：Next.js 前端工作台，包含上传、搜索、问答、图谱、概念卡片和设置页面。
- `apps/api`：FastAPI 后端，负责课程管理、文件导入、解析、切块、向量化、检索、知识图谱构建和智能体问答。
- `apps/worker`：可选后台组件，包含 Celery 导入 Worker 和目录监听器。
- `packages/shared`：前后端共享的 TypeScript 数据契约。
- `infra`：本地基础设施，包含 PostgreSQL、Redis、Qdrant 的 Docker Compose 配置。

## 数据模型

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
    IngestionJob ||--o{ IngestionCompensationLog : compensations
    Course ||--o{ IngestionCompensationLog : vector_compensations
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

关键约束：

- `Course.name` UNIQUE — 课程名唯一
- `Concept(course_id, normalized_name)` UNIQUE — 同课程内概念去重
- `DocumentVersion(document_id, version)` UNIQUE — 文档版本号唯一
- 所有主键使用 `UUID v4`（`String(36)`），分布式友好
- `TimestampMixin` 自动维护 `created_at` / `updated_at`
- 模式演进通过启动时的 `SCHEMA_PATCHES`（`ALTER TABLE ADD COLUMN`）实现

## 跨存储协调

系统涉及三类存储，各有不同的事务保障：

| 存储 | 技术 | 事务保障 |
|------|------|----------|
| 关系数据库 | SQLAlchemy (PG/SQLite) | `autocommit=False`，完整 ACID |
| 向量索引 | Qdrant / FallbackJSON | 无分布式事务 |
| 文件系统 | 本地磁盘 | 无事务 |

**跨存储一致性策略**：

- **图谱构建**采用单事务模式（DELETE→INSERT→单次 COMMIT），ACID 合规。
- **文档解析**采用两阶段激活：先提交 inactive 的新版本和 chunks，向量 upsert 成功后再激活新版本并停用旧版本。
- **检索层**对向量存储返回的结果用数据库做二次验证（过滤已删除或不活跃的 chunk），防御跨存储不一致。
- **批量操作**的异常处理采用 `rollback()→get()→标记 failed→commit()` 模式，单文件失败不污染批次。
- **进程重启恢复**：`finalize_interrupted_batches()` 在 FastAPI 生命周期启动时处理 pending 的向量补偿日志，并将未完成的批次标记为失败。

## 并发与异步模型

```
FastAPI (uvicorn) ─── 异步 API 路由
  ├── BackgroundTasks ─── async callable / Celery 入口
  │     └── run_uploaded_files_ingestion（复用事件循环）
  ├── LLM 调用 ─── httpx.AsyncClient / asyncio.to_thread(curl)
  ├── 图谱抽取 ─── asyncio.gather() + Semaphore(2)
  └── QA 流式输出 ─── SSE token + trace 事件
```

**并发控制机制**：

| 机制 | 位置 | 保护范围 |
|------|------|----------|
| SQLAlchemy 会话 | `autocommit=False` | 数据库事务隔离 |
| `source_path` 应用锁 | `ingest_file` | 同一文件导入串行化 |
| 非终态批次互斥 | `create_sync_batch` / `create_uploaded_files_batch` | 同课程同一时间只保留一个活跃导入批次 |
| `asyncio.Semaphore(2)` | `extract_llm_graph_payloads` | LLM API 并发限流 |
| `portalocker` / 进程锁 | `FallbackVectorStore` | fallback JSON 向量文件多进程互斥 |
| LRU 锁注册表 | `_VECTOR_FILE_LOCKS` / `_SOURCE_PATH_LOCKS` | 长期运行时限制锁表增长 |
| fsync 文件写入 | `_write`（临时文件+替换+fsync） | fallback 向量文件写入完整性 |

**设计决策**：智能体流程中每个 LangGraph 节点执行时都会 `db.commit()` 更新 `current_node` 和追踪事件。这是有意的可观测性设计，使前端能通过 `/tasks/{run_id}` 实时追踪智能体执行进度。

## 降级策略

默认降级功能是上锁的：

```env
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

这意味着系统不会静默降级到假向量化、抽取式答案、本地 JSON 向量索引或 SQLite。默认要求：

- `OPENAI_API_KEY` 可用，用于向量化、对话和图谱抽取。
- `QDRANT_URL` 指向可访问的 Qdrant 实例。
- `DATABASE_URL` 指向可访问的 PostgreSQL 实例。

只有在本地离线调试时才建议显式解锁：

```env
ENABLE_MODEL_FALLBACK=true
ENABLE_DATABASE_FALLBACK=true
```

解锁后，系统可能使用确定性本地哈希向量、抽取式回退答案，或 `data/<课程名>/ingestion/vector_index.json` 作为向量索引兜底。这些结果只适合开发验证，不适合作为正式知识库质量判断。

数据库层的 SQLite 降级同样必须显式开启。生产环境不要依赖 SQLite fallback，也不要把 fallback 兼容性测试作为默认验收门禁。

## 数据目录结构

默认数据根目录是 `data/`。每门课程会创建独立目录：

```text
data/
  <课程名>/
    storage/       上传文件和归档副本
    ingestion/     解析 JSON 和可选的降级向量索引 vector_index.json
    source/        可选的监听源文件目录
  qdrant/          Qdrant 持久化存储
  postgres/        PostgreSQL 持久化存储
  redis/           Redis 持久化存储
```

主要持久化位置：

- 图谱节点和关系：PostgreSQL 表 `concepts`、`concept_relations`。
- 问答历史：PostgreSQL 表 `qa_sessions`，消息在 `transcript` 字段。
- 智能体运行轨迹：`agent_runs`、`agent_trace_events`。
- 文档、版本、切块和导入批次：`documents`、`document_versions`、`chunks`、`ingestion_batches`、`ingestion_jobs`。
- 向量索引：Qdrant 集合 `knowledge_chunks`。

## 环境要求

- Node.js `>= 20.9.0`
- Python `>= 3.11`
- `uv` Python 依赖管理工具
- Docker Desktop 或支持 Compose v2 的 Docker Engine

如需安装 `uv`：

```powershell
python -m pip install uv
```

## 配置

创建根目录环境变量文件：

```powershell
Copy-Item .env.example .env
```

最小本地开发配置：

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
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
API_KEYS=
```

如果使用 DashScope 或其他 OpenAI 兼容端点，请相应设置 `OPENAI_BASE_URL` 和 `OPENAI_API_KEY`。

## 安装依赖

在仓库根目录安装前端工作区依赖：

```powershell
npm install
```

安装后端 API 依赖：

```powershell
cd apps/api
uv sync
```

首次启用检索重排前，需要下载 Cross-Encoder reranker 模型。推荐显式设置 `HF_HOME`，这样模型缓存位置可控；如果不设置，Hugging Face 会使用当前用户的默认缓存目录。

```powershell
cd apps/api
$env:HF_HOME="C:\Study\KnowledgeGraph\models\huggingface"
# 国内网络可选：
# $env:HF_ENDPOINT="https://hf-mirror.com"
@'
from sentence_transformers import CrossEncoder
CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=512)
print("BAAI/bge-reranker-v2-m3 downloaded")
'@ | .\.venv\Scripts\python.exe -
```

启动 API 时如果希望复用上述缓存，也需要带上相同的 `HF_HOME`：

```powershell
cd apps/api
$env:HF_HOME="C:\Study\KnowledgeGraph\models\huggingface"
uv run uvicorn app.main:app --reload
```

如需后台导入功能，安装 Worker 依赖：

```powershell
cd apps/worker
uv sync
```

## 构建并启动后端基础设施

当前仓库的 Docker Compose 仅包含后端基础设施：PostgreSQL、Redis 和 Qdrant。暂不包含 API/Web/Worker 应用的 Dockerfile。

拉取基础设施镜像：

```powershell
docker compose -f infra/docker-compose.yml pull
```

启动后端基础设施：

```powershell
docker compose -f infra/docker-compose.yml up -d
```

检查状态：

```powershell
docker compose -f infra/docker-compose.yml ps
```

镜像更新后重建容器：

```powershell
docker compose -f infra/docker-compose.yml pull
docker compose -f infra/docker-compose.yml up -d --force-recreate
```

`docker compose build` 对当前基础设施配置无效，因为三个服务均直接使用公共镜像（`postgres:16`、`redis:7`、`qdrant/qdrant:v1.13.2`），未定义本地构建上下文。

## 启动应用服务

推荐使用仓库根目录的 Windows 启动器：

```powershell
.\start-app.ps1
```

启动器会运行：

- API 服务：`http://127.0.0.1:8000`
- Web 前端：`http://127.0.0.1:3000`
- 浏览器默认打开 `/graph` 页面

不打开浏览器启动：

```powershell
.\start-app.ps1 -NoBrowser
```

使用自定义端口：

```powershell
.\start-app.ps1 -BackendPort 8001 -FrontendPort 3001 -OpenPath "/search"
```

手动启动 API：

```powershell
cd apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

手动启动前端：

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000/api"
npm run dev --workspace web -- --hostname 127.0.0.1 --port 3000
```

可选 Worker：

```powershell
cd apps/worker
uv run celery -A worker_app.celery_app worker --loglevel=info
```

可选目录监听导入：

```powershell
cd apps/worker
uv run python -m worker_app.watcher
```

## 构建前端

类型检查并构建 Web 应用：

```powershell
npm run typecheck:web
npm run build:web
```

构建后启动生产模式 Next.js 服务：

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000/api"
npm run start --workspace web
```

## 导入流程

```mermaid
sequenceDiagram
    participant 用户
    participant 前端
    participant API
    participant 文件存储 as 课程存储
    participant 数据库 as PostgreSQL
    participant 模型 as 模型 API
    participant Qdrant

    用户->>前端: 上传或同步课程文件
    前端->>API: POST /api/files/upload 或 /api/ingestion/parse-uploaded-files
    API->>文件存储: 保存归档副本
    API->>API: 解析文档（PDF/PPTX/DOCX/MD/Notebook/OCR）
    API->>API: 切块、去重和过滤（text 800/120，code 700/100）
    API->>模型: 使用元数据增强文本批量异步生成向量
    API->>Qdrant: 写入向量
    API->>数据库: 存储文档、版本、切块（commit）
    API->>模型: 抽取概念图谱（Semaphore 限流）
    API->>数据库: 重建图谱（单事务 DELETE→INSERT→COMMIT）
    API-->>前端: 通过 SSE 流推送批次状态
```
## RAG 检索增强生成架构

下图展示了系统完整的 RAG（检索增强生成）管线，包含离线索引阶段和在线查询阶段：

```mermaid
flowchart TB
    subgraph IDX["离线索引阶段 - 导入时执行"]
        direction TB
        SRC["源文件<br/>PDF / PPTX / DOCX / MD / Notebook / HTML / 图片"]
        SRC --> PARSE["文档解析器<br/>结构化提取 + OCR"]
        PARSE --> CHUNK["语义切块 + 去重/过滤<br/>text=800/120<br/>code=700/100"]
        CHUNK --> EMB["元数据增强向量化<br/>Document / Chapter / Section / Source Type / Content Kind<br/>DB chunk.content 保持原文"]
        CHUNK --> META["元数据提取<br/>章节 / 类型 / 页码 / 标题"]
        EMB --> VEC_W[("向量存储写入<br/>Qdrant / FallbackJSON")]
        META --> DB_W[("关系数据库写入<br/>Document, Chunk")]
        CHUNK --> LLM_G["LLM 图谱抽取<br/>Semaphore 限流"]
        LLM_G --> GRAPH_W[("知识图谱写入<br/>Concept, Relation<br/>单事务 ACID")]
    end

    subgraph QRY["在线查询阶段 - 用户提问时执行"]
        direction TB
        Q["用户问题"] --> QA["查询分析<br/>分词 / 意图识别 / query type"]
        QA --> ROUTE{"路由决策"}
        ROUTE -->|直答 / 澄清| DIRECT["直接回答<br/>无需检索"]
        ROUTE -->|笔记 / 习题 / 通用| REWRITE["查询改写<br/>LLM 上下文消歧"]
        ROUTE -->|多跳 / 比较 / 关系| MULTI_HOP["多跳拆解<br/>子查询生成"]
        REWRITE --> DENSE["稠密检索<br/>向量相似度"]
        REWRITE --> LEX["BM25 词面检索<br/>数据库 active chunks"]
        MULTI_HOP --> DENSE
        MULTI_HOP --> LEX
        DENSE --> WSF["WSF 融合排序<br/>query type alpha + min-max 归一化"]
        LEX --> WSF
        WSF --> RERANK["Cross-Encoder 重排<br/>BAAI/bge-reranker-v2-m3"]
        RERANK --> GRAPH_EXT["GraphRAG 扩展<br/>待升级：暂不作为主检索排序路径"]
        GRAPH_EXT --> GRADE["文档评分<br/>词项重叠 + 分数阈值"]
        GRADE -->|文档不足, 重试次数不足| RETRY["重试规划<br/>扩展查询词"]
        RETRY --> DENSE
        GRADE -->|有相关文档| CTX["上下文合成<br/>top-K 摘录拼接"]
        CTX --> GEN["答案生成<br/>LLM + 课程上下文约束"]
        GEN --> CITE["引用检查<br/>过滤无关引用"]
        CITE --> SELF["自检<br/>状态闭合"]
    end

    subgraph STR["存储层"]
        VEC[("Qdrant<br/>向量索引")]
        DB[("PostgreSQL<br/>元数据 + 切块")]
        KG[("知识图谱<br/>概念 + 关系")]
    end

    VEC_W -.-> VEC
    DB_W -.-> DB
    GRAPH_W -.-> KG
    DENSE --> VEC
    LEX --> DB
    GRAPH_EXT -. "待升级" .-> KG
    GRADE -->|二次验证| DB
```

**核心设计要点**：

| 阶段 | 关键机制 | 说明 |
|------|----------|------|
| 切块 | 评测优选策略 `chunk_800_metadata_enriched_v1` | 普通文本 `chunk_size=800, overlap=120`；代码块 `chunk_size=700, overlap=100`；继续使用递归分隔符和 Markdown 标题层级 |
| 去重/过滤 | 文档与 chunk 两层清理 | 文档按规范化标题 + checksum 去重；chunk 按规范化内容 hash 去重；过滤目录页、页码/图号、乱码、过短低信息块、notebook output 和低信息纯代码 |
| 向量文本 | 元数据增强，原文入库 | embedding input 追加 Document / Chapter / Section / Source Type / Content Kind；`chunks.content` 保持原始 chunk 文本，Qdrant payload 标记 `embedding_text_version=metadata_enriched_v1` |
| 向量化 | 批量异步；fallback 默认禁用 | OpenAI 兼容 API；`ENABLE_MODEL_FALLBACK=false` 时失败直接暴露，不静默降级 |
| 检索 | Dense + BM25 + WSF + reranker | 两路独立召回，按 query type 配置 Dense/BM25 权重，min-max 归一化后 WSF 融合，再用 `BAAI/bge-reranker-v2-m3` 重排 |
| 评分 | 词项重叠 + 内容类型加权 | 文本 +1.1 / 代码 -1.8 / 标题命中 +1.4 |
| 一致性 | 检索二次验证 | 向量结果与数据库交叉验证，过滤已删除和不活跃切块 |
| 图谱 | 单事务全量重建；GraphRAG 待升级 | DELETE→INSERT→COMMIT，ACID 合规；图谱浏览和关系存储已可用，图谱增强检索仍需进一步评估和升级后再纳入主排序链路 |

### GraphRAG 工作流

当前图谱构建、图谱浏览和关系存储已落地，但 GraphRAG 增强检索仍标记为待升级能力。上一轮评估显示，直接把图谱邻居扩展并入主排序链路可能带来噪声，因此当前主检索路径优先使用 Dense + BM25 + WSF + reranker；GraphRAG 后续应升级为“语义门控 + 证据支持校验 + 重排后注入”的受控路径。

计划升级后的单跳扩展（1-Hop Expansion）机制如下：

```mermaid
graph LR
    BASE[混合检索基础 Chunk] --> REL[提取关联的 1 跳邻居关系]
    REL --> NEW[发现新证据 Chunk]
    NEW --> BOOST[计算图谱提权分]
    BOOST --> VERIFY[证据支持校验]
    VERIFY --> MERGE[与基础结果受控融合重排]
    BASE --> MERGE
```

## 智能体问答流程

```mermaid
graph LR
    A[查询分析器] --> B[路由器]
    B -->|常规检索| C[查询改写器]
    B -->|多跳/关系| C2[多跳拆解器]
    B -->|直答/澄清| G[答案生成器]
    C --> D[混合检索器<br/>Dense+BM25+WSF+reranker]
    C2 --> D2[图谱增强检索器<br/>GraphRAG 待升级]
    D --> E[文档评分器]
    D2 --> E
    E -->|有文档| F[上下文合成器]
    E -->|重试 < 2| R[重试规划器]
    R --> D
    F --> G
    G --> H[引用检查器]
    H --> I[自检器]
```

每个节点执行时实时更新 `AgentRun.current_node` 和 `AgentTraceEvent`（每节点一次 commit），支持前端进度追踪。

## 主要 API 端点

- `GET /api/courses` — 课程列表
- `POST /api/courses` — 创建课程
- `GET /api/courses/current/dashboard?course_id=...` — 课程仪表盘
- `GET /api/courses/current/graph?course_id=...` — 课程图谱
- `GET /api/graph/chapters/{chapter}?course_id=...` — 章节图谱
- `GET /api/graph/nodes/{concept_id}?course_id=...` — 概念节点详情
- `GET /api/concepts?course_id=...` — 概念卡片列表
- `POST /api/files/upload?course_id=...` — 上传文件
- `POST /api/ingestion/parse-uploaded-files` — 解析已上传文件
- `POST /api/ingestion/parse-storage?course_id=...` — 解析存储目录
- `GET /api/ingestion/batches/{batch_id}` — 批次状态
- `GET /api/ingestion/batches/{batch_id}/logs` — 批次日志（SSE 流）
- `POST /api/search` — 混合检索
- `POST /api/qa` — 智能体问答
- `POST /api/qa/stream` — 流式智能体问答（SSE 流）
- `POST /api/agent` — 智能体调用
- `GET /api/tasks/{run_id}` — 智能体运行状态
- `GET /api/sessions?course_id=...` — 会话列表
- `GET /api/sessions/{session_id}/messages` — 会话消息
- `DELETE /api/sessions/{session_id}` — 删除会话
- `GET /api/settings/model` — 模型设置
- `PUT /api/settings/model` — 更新模型设置

## 开发说明

- `.env`、`data/`、本地数据库和生成的日志文件不要提交到 Git。
- `ingestion/` 目录包含派生的解析产物，可从存储的源文件重新生成。
- `storage/` 目录包含上传或复制的源文件，删除后将无法重新导入。
- API 在启动时使用轻量级模式补丁（`SCHEMA_PATCHES` + `ALTER TABLE ADD COLUMN`）而非 Alembic 迁移。
- 认证和生产级授权尚未实现。
- `FallbackVectorStore` 使用线程级锁和原子临时文件写入；适用于单进程部署，不适用于多 Worker 配置。
- `finalize_interrupted_batches()` 在启动时运行，将未完成的批次标记为失败，提供导入管线的崩溃恢复。
