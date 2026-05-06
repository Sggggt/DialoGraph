[英文版](./README.en.md) | **中文版**

<p align="center">
  <img src="./assets/diagraph-logo.svg" alt="DialoGraph 标志" width="132" height="132">
</p>

# DialoGraph

面向本地课程资料的 Docker 化知识库系统。DialoGraph 将 PDF、课件、文档、网页、笔记本和图片资料解析为可检索文本块、向量索引、概念图谱和带引用的问答结果。

> 默认运行在真实 PostgreSQL、Qdrant、Redis 和 OpenAI 兼容模型接口之上。模型降级和数据库降级默认关闭；质量评测与生产运行不使用模拟向量、抽取式替代回答或本地 JSON 替代检索。

## 快速概览

| 维度 | 当前实现 |
| --- | --- |
| 运行方式 | Docker Compose，全栈容器化 |
| 后端 | FastAPI，容器内系统 Python，无虚拟环境 |
| 前端 | Next.js，通过接口服务访问课程数据 |
| 数据库 | PostgreSQL 16，保存课程、文件、文本块、图谱、问答记录 |
| 向量库 | Qdrant 1.17.1，集合名 `knowledge_chunks` |
| 缓存与任务 | Redis 7 |
| 模型接口 | OpenAI 兼容接口，负责向量、对话、图谱抽取 |
| 检索策略 | 子块召回、融合、精排，再装配父块上下文 |
| 图谱策略 | 概念、别名、关系和证据文本块关联 |
| 质量要求 | 无降级、无零向量、数据库与向量库数量一致 |

## 技术栈

| 层级 | 技术选型 | 作用 |
| --- | --- | --- |
| 前端 | Next.js、React、TypeScript | 课程管理、文件导入、检索问答、图谱查看和运行时设置 |
| 后端接口 | FastAPI、Pydantic、SQLAlchemy | REST 接口、数据校验、事务管理、导入任务和智能体编排 |
| 数据库 | PostgreSQL 16 | 课程、文件版本、文本块、图谱、问答会话和运行轨迹 |
| 向量检索 | Qdrant 1.17.1 | 父块和子块向量存储、向量召回、向量健康检查 |
| 词面检索 | PostgreSQL 文本数据 + `rank_bm25` | 子块 BM25 召回和词面匹配补充 |
| 缓存与任务 | Redis 7 | 运行时缓存、任务协调和服务依赖 |
| 文档解析 | PyMuPDF、PPTX/DOCX 解析器、Markdown/HTML/Notebook 解析 | 将不同格式资料转换为结构化章节和文本段 |
| 模型接口 | OpenAI 兼容 Embedding / Chat API | 向量生成、摘要关键词生成、概念关系抽取、问答生成 |
| 精排 | 轻量精排、可选 Cross-Encoder | 对融合候选进行相关性重排 |
| 部署 | Docker Compose | 固定服务边界、依赖版本和本地持久化路径 |
| 测试 | pytest、容器内系统 Python | 后端单测、链路测试和无降级质量门禁 |

## 系统架构

```mermaid
flowchart TB
    USER["用户浏览器"] --> WEB["网页应用<br/>course-kg-web"]
    WEB -->|"HTTP / 事件流"| API["接口服务<br/>course-kg-api"]

    subgraph APP["应用层"]
        API --> ROUTES["接口路由<br/>课程 / 文件 / 导入 / 检索 / 问答 / 图谱 / 设置"]
        ROUTES --> INGEST["导入流水线<br/>解析 -> 切块 -> 知识增强 -> 向量写入 -> 图谱抽取"]
        ROUTES --> SEARCH["检索流水线<br/>子块召回 -> 融合 -> 精排 -> 父块上下文"]
        ROUTES --> AGENT["智能体问答<br/>分析 -> 路由 -> 检索 -> 证据评估 -> 回答 -> 轨迹"]
        ROUTES --> GRAPH["知识图谱<br/>概念 / 关系 / 证据 / 节点详情"]
    end

    subgraph INFRA["基础设施"]
        POSTGRES[("PostgreSQL 16<br/>元数据、文本块、图谱、问答记录")]
        QDRANT[("Qdrant 1.17.1<br/>向量集合 knowledge_chunks")]
        REDIS[("Redis 7<br/>任务与运行时缓存")]
    end

    subgraph STORAGE["宿主机持久化"]
        DATA["data/<br/>课程文件、解析产物、数据库数据"]
        MODEL_CACHE["data/models/huggingface<br/>精排模型缓存"]
    end

    subgraph MODEL["外部模型接口"]
        LLM["OpenAI 兼容接口<br/>向量 / 对话 / 图谱抽取"]
    end

    API --> POSTGRES
    API --> QDRANT
    API --> REDIS
    API -->|"OPENAI_BASE_URL"| LLM
    API --- DATA
    API --- MODEL_CACHE
    POSTGRES --- DATA
    REDIS --- DATA
```

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多格式解析 | 支持 PDF、PPT/PPTX、DOCX、Markdown、TXT、Notebook、HTML 和图片资料 |
| 父子块切分 | 父块保留完整上下文，子块承担精确召回和精排 |
| 上下文向量文本 | 向量输入包含文件元数据、父块摘要、相邻子块摘要、关键词、表格和公式标记 |
| 混合检索 | Qdrant 子块向量召回与 PostgreSQL 子块词面召回融合 |
| 精排 | 默认轻量精排，可启用 Cross-Encoder 精排模型 |
| 图谱增强 | 通过证据文本块连接概念和关系，用于关系、比较和多跳问题 |
| 可观测问答 | 记录智能体节点轨迹、检索审计、模型调用审计和引用信息 |
| 运行时检查 | 提供健康检查、配置检查、降级状态检查和精排状态检查 |

## 核心算法

### 分层切块算法

DialoGraph 使用父子块结构处理课程资料的上下文跨度问题：

1. 解析器先把文件转换为 `ParsedSection`，保留章节、页码、来源类型、表格和公式等结构信息。
2. 每个结构段生成一个父块，父块保存完整章节或自然段落。
3. 父块内部继续生成多个子块，子块承担精确召回和精排。
4. Markdown 和 Notebook 按标题层级切分；普通长文本按语义边界、句子边界和安全长度切分。
5. 当 `SEMANTIC_CHUNKING_ENABLED=true` 且文本长度达到 `SEMANTIC_CHUNKING_MIN_LENGTH` 时，系统可使用基于 embedding 相似度的语义切分。

这种设计避免只使用大块导致召回不准，也避免只使用小块导致回答上下文不足。

### 上下文增强向量

子块向量不是只嵌入子块原文，而是通过 `contextual_embedding_text()` 构造上下文增强文本：

```text
文件元数据
章节与来源类型
子块正文
父块摘要或父块正文
前后相邻子块摘要
关键词
表格和公式标记
```

父块保留自身内容、摘要和关键词。子块继承父块摘要，并补充相邻子块摘要，用于缓解小块缺少上下文的问题。当前向量文本版本为 `contextual_enriched_v2`。

### Small-to-Big 检索

主检索链路采用 small-to-big 策略：

```mermaid
flowchart LR
    Q["查询"] --> DENSE["子块向量召回"]
    Q --> BM25["子块 BM25 召回"]
    DENSE --> FUSION["加权融合"]
    BM25 --> FUSION
    FUSION --> RERANK["精排"]
    RERANK --> PARENT["按 parent_chunk_id 拉取父块"]
    PARENT --> RESULT["子块证据 + 父块上下文"]
```

算法要点：

- Qdrant 和 BM25 默认只召回子块，避免父块和子块在同一候选池中互相竞争。
- 向量结果和词面结果通过加权分数融合；仅有向量命中时保持 dense-only 路径。
- 精排阶段处理融合后的子块候选。
- 最终结果按 `parent_chunk_id` 装配 `parent_content`，让回答模型看到完整上下文。
- 返回 metadata 中包含 `retrieval_granularity=child_with_parent_context`、精排分数和模型调用审计。

### 图谱增强检索

图谱不是直接替代文本证据，而是用来扩展候选证据：

1. 先运行混合检索得到文本候选。
2. 根据命中文本块的 `evidence_chunk_id` 找到相关概念和关系。
3. 扩展一跳关系，收集关系证据文本块。
4. 将关系证据以 `graph_boost` 合并回候选集。
5. 精排后统一装配父块上下文。

这种做法让图谱增强仍然受文本证据约束，避免只凭图关系生成没有证据支撑的回答。

### 智能体问答

智能体问答把检索问答拆成可观测步骤：

```text
问题分析 -> 路由 -> 查询改写 -> 检索 -> 证据评估 -> 上下文合成 -> 回答生成 -> 引用检查 -> 自检
```

每个节点都会写入运行轨迹，前端可以展示当前节点、检索证据、模型审计和最终引用。

## 数据流

```mermaid
sequenceDiagram
    participant 用户
    participant 网页
    participant 接口
    participant 文件 as 文件存储
    participant 数据库 as PostgreSQL
    participant 向量库 as Qdrant
    participant 模型 as 模型接口

    用户->>网页: 上传或选择课程文件
    网页->>接口: 创建导入任务
    接口->>文件: 保存原始文件
    接口->>数据库: 创建文件、版本、批次和任务记录
    接口->>接口: 解析文件并生成父块、子块
    接口->>模型: 生成摘要、关键词和向量
    接口->>向量库: 写入父块和子块向量
    接口->>数据库: 激活文本块和文件版本
    接口->>模型: 抽取概念和关系
    接口->>数据库: 保存图谱与证据关联
    接口-->>网页: 推送导入日志和最终状态
```

导入过程使用显式事务和文件级锁。同一课程同一时间只保留一个非终态导入批次。Qdrant 写入失败会记录补偿信息，系统启动时会检查未完成批次。

## 数据模型

```mermaid
erDiagram
    Course ||--o{ Document : has
    Course ||--o{ Concept : has
    Course ||--o{ IngestionBatch : has
    Course ||--o{ QASession : has
    Document ||--o{ DocumentVersion : versions
    DocumentVersion ||--o{ Chunk : chunks
    Chunk ||--o{ Chunk : children
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source
    Concept ||--o{ ConceptRelation : target
    IngestionBatch ||--o{ IngestionJob : jobs
    IngestionJob ||--o{ IngestionCompensationLog : compensations
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

| 表 | 作用 |
| --- | --- |
| `courses` | 课程工作区 |
| `documents` / `document_versions` | 文件元数据和文件版本 |
| `chunks` | 父块、子块、摘要、关键词、向量状态和证据文本 |
| `concepts` / `concept_aliases` / `concept_relations` | 概念、别名、关系和证据块关联 |
| `ingestion_batches` / `ingestion_jobs` | 批量导入和单文件任务 |
| `ingestion_logs` / `ingestion_compensation_logs` | 事件流日志和补偿记录 |
| `qa_sessions` / `agent_runs` / `agent_trace_events` | 问答会话、智能体运行和节点轨迹 |

## 切块与向量

| 阶段 | 行为 |
| --- | --- |
| 结构解析 | 按文件类型解析章节、页面、表格、公式、单元格和图片文本 |
| 父块生成 | 保留完整章节或自然段落，用于回答上下文 |
| 子块生成 | 在父块内按语义边界、句子边界和安全长度切分 |
| 知识增强 | 为文本块生成摘要、关键词和内容类型标记 |
| 向量输入 | 由 `contextual_embedding_text()` 构造上下文增强文本 |
| 版本标记 | 当前向量文本版本为 `contextual_enriched_v2` |

检索只让子块进入召回和精排，结果再附带父块信息：

```text
parent_chunk_id
parent_content
retrieval_granularity=child_with_parent_context
```

## 检索与问答

```mermaid
flowchart LR
    Q["用户问题"] --> ANALYZE["问题分析"]
    ANALYZE --> DENSE["子块向量召回"]
    ANALYZE --> LEXICAL["子块词面召回"]
    DENSE --> FUSION["加权融合"]
    LEXICAL --> FUSION
    FUSION --> RERANK["精排"]
    RERANK --> CONTEXT["装配父块上下文"]
    CONTEXT --> ANSWER["生成回答和引用"]
    ANALYZE -. "关系、比较、多跳" .-> GRAPH["图谱增强检索"]
    GRAPH --> RERANK
```

| 路径 | 使用场景 | 输出 |
| --- | --- | --- |
| 混合检索 | 定义、公式、例子、事实型问题 | 子块证据、父块上下文、精排分数 |
| 图谱增强检索 | 关系、比较、多跳问题 | 文本证据、相关概念、关系证据 |
| 智能体问答 | 需要问题分析、改写、证据评估和追踪的问答 | 回答、引用、模型审计、节点轨迹 |

## 技术优势

| 优势 | 具体体现 |
| --- | --- |
| 上下文和精度兼顾 | 子块负责精确召回，父块负责完整上下文，减少小块断章取义和大块召回粗糙的问题 |
| 证据优先 | 回答基于真实文本块和父块上下文，图谱关系也必须回到证据块 |
| 对课程资料友好 | 保留章节、页码、表格、公式、Notebook 单元和来源类型，适合教学资料检索 |
| 可审计 | 检索结果携带分数、父块、精排、模型调用、降级状态和引用信息 |
| 可维护 | Docker 固定服务边界，PostgreSQL/Qdrant/Redis 职责清晰，脚本可独立做重嵌入和质量检查 |
| 质量门禁明确 | 禁用 fallback、检查零向量、检查 DB/Qdrant 数量一致性，避免系统在静默退化状态下给出结果 |
| 可扩展 | 精排器、语义切块、图谱增强和模型接口都通过配置或服务层隔离 |

## 配置

复制配置模板：

```powershell
Copy-Item .env.example .env
```

常用配置：

| 变量 | 说明 |
| --- | --- |
| `API_HOST_PORT` / `WEB_HOST_PORT` | 宿主机访问端口 |
| `DATABASE_URL` | PostgreSQL 连接地址 |
| `QDRANT_URL` / `QDRANT_COLLECTION` | Qdrant 地址和集合名 |
| `REDIS_URL` | Redis 地址 |
| `COURSE_NAME` | 默认课程名 |
| `DATA_ROOT` | 本地数据根目录 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI 兼容模型接口 |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | 向量模型和维度 |
| `CHAT_MODEL` | 对话模型 |
| `ENABLE_MODEL_FALLBACK` | 模型降级开关，默认 `false` |
| `ENABLE_DATABASE_FALLBACK` | 数据库降级开关，默认 `false` |
| `RERANKER_ENABLED` / `RERANKER_MODEL` | Cross-Encoder 精排配置 |
| `SEMANTIC_CHUNKING_ENABLED` | 语义切块开关 |
| `SEMANTIC_CHUNKING_MIN_LENGTH` | 语义切块最小文本长度 |

Docker Compose 会在 API 容器内使用服务名覆盖基础设施地址：

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/course_kg
QDRANT_URL=http://qdrant:6333
REDIS_URL=redis://redis:6379/0
```

如果宿主机可以访问模型供应商，但容器内网络连接不稳定，可以启用模型桥接：

```env
MODEL_BRIDGE_ENABLED=true
MODEL_BRIDGE_PORT=8765
```

模型桥接只转发真实模型接口，不替代模型，也不是降级路径。

## 运行

```powershell
# 构建镜像
docker compose -f infra/docker-compose.yml build api web

# 启动全栈
docker compose -f infra/docker-compose.yml up -d postgres redis qdrant api web

# 查看容器状态
docker ps

# 检查健康状态
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/settings/runtime-check
```

网页地址：

```text
http://127.0.0.1:3000
```

## 测试与质量门禁

测试在 API 容器内使用系统 Python 运行：

```powershell
docker exec course-kg-api python -m pytest apps/api/tests
```

常用质量检查：

```powershell
docker exec course-kg-api python scripts/quality_gate.py
docker exec course-kg-api python scripts/analyze_chunk_quality.py
```

验收条件：

| 检查项 | 期望 |
| --- | --- |
| 健康状态 | `/api/health` 返回 `degraded_mode=false` |
| 运行时配置 | `/api/settings/runtime-check` 没有阻断项 |
| 模型降级 | `ENABLE_MODEL_FALLBACK=false` |
| 数据库降级 | `ENABLE_DATABASE_FALLBACK=false` |
| 向量调用 | 检索审计显示真实调用向量接口 |
| 降级原因 | `fallback_reason` 为空 |
| 向量健康 | Qdrant 没有零向量 |
| 数量一致性 | 活跃文本块数量与有效向量数量一致 |

## 版本库规则

不进入版本库：

- `.env` 和本地密钥。
- `data/`、数据库文件、模型缓存和运行输出。
- `node_modules/`、`.next/`、缓存目录和测试报告。
- `comparative_experiment/`。

应进入版本库：

- 源码、测试、脚本、Docker 编排、配置模板和 README。
