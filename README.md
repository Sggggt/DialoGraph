[English](./README.en.md) | **中文**

<p align="center">
  <img src="./assets/diagraph-logo.svg" alt="DialoGraph 标志" width="132" height="132">
</p>

<h1 align="center">DialoGraph</h1>

DialoGraph 是面向本地课程资料的 Docker 化知识基础设施。系统把 PDF、课件、文档、网页、Notebook、图片和 Markdown 解析为可检索文本块、Qdrant 向量、PostgreSQL 稀疏知识图谱和带引用问答结果。

默认运行依赖真实 PostgreSQL、Qdrant、Redis 和 OpenAI 兼容模型接口。模型 fallback 与数据库 fallback 默认关闭；生产质量验证不使用零向量、fake embedding、本地 JSON 检索或抽取式替代回答。

## 快速概览

| 维度 | 实现 |
| --- | --- |
| 运行方式 | Docker Compose，全栈容器化 |
| 后端 | FastAPI、Pydantic、SQLAlchemy、NetworkX |
| 前端 | Next.js 16.2.4、React 19、TypeScript、TanStack Query、ECharts |
| 数据库 | PostgreSQL 16，保存课程、文件版本、文本块、图谱、问答会话和运行轨迹 |
| 向量库 | Qdrant 1.17.1，集合 `knowledge_chunks` |
| 缓存与协调 | Redis 7 |
| 模型接口 | OpenAI 兼容 Embedding / Chat API |
| 检索 | child chunk dense + BM25 召回，融合、精排，再装配 parent context |
| 图谱 | LLM 抽候选，chunk 向量建语义图，图算法做构图、消冗、社区、中心性和隐式关系 |

## 技术栈

| 层 | 技术 | 职责 |
| --- | --- | --- |
| 前端 | Next.js 16.2.4、React 19、TypeScript、TanStack Query、ECharts | 课程管理、上传解析、搜索、问答、图谱浏览、运行时配置 |
| API | FastAPI、Pydantic、SQLAlchemy | REST / SSE 接口、强类型校验、事务编排、导入任务、检索与问答编排 |
| 图算法 | NetworkX、NumPy、SciPy | 稀疏构图、连通分量、Louvain、谱聚类、中心性、Dijkstra 隐式关系 |
| 数据库 | PostgreSQL 16 | 课程、文件版本、chunks、图谱、问答会话、运行轨迹和补偿记录 |
| 向量检索 | Qdrant 1.17.1 | parent / child chunk 向量、dense recall、向量健康检查 |
| 词面检索 | PostgreSQL 文本数据、BM25 | child chunk 词面召回与混合检索融合 |
| 缓存与协调 | Redis 7 | 运行态缓存、任务协调、服务依赖 |
| 解析 | PyMuPDF、PPTX / DOCX / Markdown / HTML / Notebook 解析器、OCR 路径 | 把异构课程资料转成结构化章节与文本 |
| 模型接口 | OpenAI 兼容 Embedding / Chat API | 向量化、摘要、关键词、实体候选、关系候选、回答生成 |
| 精排 | 轻量精排、可选 Cross-Encoder | 对融合候选按相关性重排 |
| 部署 | Docker Compose | 固定服务边界、依赖版本和本地持久化 |
| 测试 | pytest、Vitest、Next build、Docker smoke | 行为回归、前后端类型契约、无 fallback 质量门禁 |

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多格式解析 | 支持 PDF、PPT/PPTX、DOCX、Markdown、TXT、Notebook、HTML 和图片资料 |
| 父子块切分 | parent chunk 保存完整上下文，child chunk 承担精确召回、精排和证据引用 |
| 语义切分 | 对长文本按结构、语义边界、句子边界和长度上限切分，可用 embedding 相似度辅助边界判断 |
| 上下文增强向量 | 向量输入包含文件元数据、章节、父块摘要、相邻子块摘要、关键词、表格和公式标记 |
| 混合检索 | Qdrant child dense recall 与 child BM25 recall 融合，统一进入精排 |
| 图谱增强 | 图谱关系必须回到证据 chunk，作为检索扩展信号而不是替代证据 |
| 图论构图 | 用稀疏图、社区、中心性、Dijkstra 和关系补全降低噪声、保留关键结构 |
| 可观测问答 | 保存检索审计、模型调用审计、agent trace、引用和失败原因 |
| 运行时检查 | 暴露健康检查、runtime-check、fallback 状态、Qdrant 状态和模型端点状态 |

## 系统架构

```mermaid
flowchart TB
    USER["用户浏览器"] --> WEB["Next.js Web<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["FastAPI<br/>course-kg-api"]

    subgraph APP["应用层"]
        API --> INGEST["导入流水线<br/>解析 -> 父子切块 -> 知识增强 -> 向量写入"]
        API --> RETRIEVAL["检索流水线<br/>dense/BM25 -> 融合 -> 精排 -> parent context"]
        API --> GRAPH["图谱流水线<br/>LLM候选 -> 向量相似图 -> 稀疏构图 -> 社区/中心性/推断"]
        API --> QA["可观测问答<br/>路由 -> 检索 -> 证据评估 -> 回答 -> 引用检查"]
    end

    subgraph STORE["存储与运行态"]
        PG[("PostgreSQL<br/>元数据、文本块、稀疏图、审计记录")]
        QD[("Qdrant<br/>chunk 向量与相似召回")]
        RD[("Redis<br/>运行态缓存与任务协调")]
        FS["data/<br/>课程文件、解析产物、本地持久化"]
    end

    subgraph MODEL["模型接口"]
        LLM["OpenAI 兼容接口<br/>embedding / chat / graph extraction"]
    end

    API --> PG
    API --> QD
    API --> RD
    API --> FS
    API --> LLM
```

## 数据流

```mermaid
sequenceDiagram
    participant 用户
    participant Web
    participant API
    participant 文件 as File Storage
    participant DB as PostgreSQL
    participant Vector as Qdrant
    participant Model as Model API

    用户->>Web: 上传或选择课程文件
    Web->>API: 创建 ingestion batch
    API->>DB: 创建 batch、job、document/version 状态
    API->>文件: 保存原始文件与解析产物
    API->>API: 解析章节、页面、表格、公式和 Notebook 单元
    API->>API: 生成 parent/child chunks 与上下文增强文本
    API->>Model: 生成摘要、关键词和 embeddings
    API->>Vector: 写入 active chunk 向量
    API->>DB: 激活 document version 与 chunks
    API->>Model: 抽取实体/关系候选
    API->>API: 运行稀疏图构建、社区、中心性和推断
    API->>DB: 保存 concepts、relations、证据和图算法字段
    API-->>Web: SSE 推送日志、进度、retry 和失败状态
```

导入过程使用显式 batch / job 状态和文件级锁。同一课程同一时间只保留一个非终态导入批次。PostgreSQL 是生命周期事实源；Qdrant 和 Redis 是派生或运行态存储，失败会记录补偿或错误上下文，不做静默降级。

## 导入、切块与向量

### 分层切块

1. 解析器把源文件转成 `ParsedSection`，保留章节、页码、来源类型、表格、公式、Notebook cell 和图片 OCR 元数据。
2. 每个结构段生成 parent chunk，保存完整章节、页面段落或自然语义段。
3. parent chunk 内部继续生成 child chunks，用于精确召回、精排和证据定位。
4. Markdown 和 Notebook 优先按标题与 cell 层级切分；普通长文本按语义边界、句子边界和安全长度切分。
5. 当 `SEMANTIC_CHUNKING_ENABLED=true` 且文本长度达到 `SEMANTIC_CHUNKING_MIN_LENGTH` 时，系统可使用 embedding 相似度辅助切分边界。

### 上下文增强向量

child 向量不只嵌入 child 原文，而是由 `contextual_embedding_text()` 生成上下文增强输入：

```text
文件元数据
章节、页码和来源类型
child chunk 正文
parent summary 或 parent content
相邻 child summaries
关键词
表格、公式和内容类型标记
```

parent chunk 保留原文、摘要和关键词。child chunk 继承 parent 语义摘要并补充邻近上下文，减少小块检索的断章取义。当前向量文本版本是 `contextual_enriched_v2`。

### 去重与幂等

导入按课程、规范化标题和 checksum 判断重复文件。未变化文件会以 `unchanged_checksum` 跳过；同标题同 checksum 的重复副本会以 `duplicate_document` 跳过，避免重复写入 chunks 和向量。强制重新解析会重新生成 document version、chunks、Qdrant 向量和图谱候选。

## 图谱构建

DialoGraph 的图谱生成遵循 evidence-first 原则：LLM 只生成候选实体和显式关系，图结构由 chunk 向量与图算法共同确定。PostgreSQL 是稀疏图事实源，Qdrant 只提供 chunk 向量与相似度信号。

```mermaid
flowchart LR
    CHUNK["Active child chunks"] --> VEC["Qdrant chunk vectors"]
    CHUNK --> LLM["LLM entity/relation candidates"]
    LLM --> ENTITY["Concept merge<br/>alias and duplicate reduction"]
    VEC --> CENTROID["Concept vector centroid"]
    ENTITY --> SPARSE["Dynamic R-NN + K-NN sparse graph"]
    CENTROID --> SPARSE
    SPARSE --> CC["Connected components<br/>noise ablation"]
    CC --> COMM["Louvain + spectral communities"]
    COMM --> CENTRAL["Centrality ranking"]
    CENTRAL --> DIJK["Dijkstra hidden links"]
    DIJK --> COMPLETE["Traversal + prompt relation completion"]
    COMPLETE --> PG["PostgreSQL graph tables"]
    PG --> UI["Community-aware graph UI"]
```

### 1. 实体与证据

每个概念保留规范名、别名、章节引用、重要度和证据 chunk 数。概念向量不是由概念名直接生成，而是由其证据 chunk 向量求质心：

$$
\mathbf{v}_e = \frac{1}{|C_e|}\sum_{c \in C_e}\mathbf{v}_c
$$

其中 $C_e$ 是支撑实体 $e$ 的 active child chunks。向量归一化后进入相似图构建。

### 2. 动态 R-NN + K-NN 稀疏构图

每个概念按证据量动态决定发出候选边数量：

$$
K_i = \mathrm{clamp}(4 + \lfloor \log_2(1 + m_i) \rfloor, 4, 12)
$$

每个概念按章节覆盖动态限制接收反向候选：

$$
R_i = \mathrm{clamp}(2 + \lfloor \log_2(1 + r_i) \rfloor, 2, 8)
$$

$m_i$ 是证据 chunk 数，$r_i$ 是章节引用数。系统保留互为近邻、通过反向接收限制的近邻，以及高置信 LLM 显式关系，从而让边数随节点数近线性增长。

### 3. 边权与图算法

边权由 LLM 置信度、语义相似度、证据支持和结构一致性组合：

$$
w_{ij}=0.45c_{ij}^{llm}+0.30s_{ij}^{sem}+0.15s_{ij}^{evidence}+0.10s_{ij}^{structure}
$$

无 LLM 显式关系时 $c_{ij}^{llm}=0$，最终 $w_{ij}$ 裁剪到 $[0,1]$。图算法阶段执行：

- 连通分量消融：移除孤立、低证据、低重要度噪声节点，同时保持每门课程有足够节点规模。
- Louvain 社区发现：用于主社区标记和前端颜色分组。
- 谱聚类：用于大连通分量和大社区的二级结构划分。
- 中心性：计算 degree、weighted degree、PageRank、betweenness、closeness，并合成 `centrality_score`。
- 图谱精简：优先保留高中心性节点、社区代表节点、跨社区桥接边和高证据节点。

### 4. 隐式关系与关系补全

Dijkstra 在非负代价图上发现 2-3 跳隐式关系：

$$
cost_{ij}=\frac{1}{0.05+w_{ij}}
$$

当端点语义相似度足够高且路径代价足够低时，系统写入 `relation_source="dijkstra_inferred"` 的 `relates_to` 边，并用路径分数修正弱边权。随后系统对高中心性节点的二跳邻域抽取证据 snippet，让 LLM 只基于给定证据做抽取式关系补全。

前端图展示按 Louvain 社区着色，节点大小反映中心性和图谱排序，虚线边表示 inferred 关系。用户可以筛选社区并快速打开关键实体详情。

## 检索与问答

```mermaid
flowchart LR
    Q["问题"] --> DENSE["child dense recall"]
    Q --> BM25["child BM25 recall"]
    DENSE --> FUSION["weighted fusion"]
    BM25 --> FUSION
    FUSION --> RERANK["rerank"]
    RERANK --> PARENT["load parent_chunk_id"]
    PARENT --> ANSWER["answer with citations"]
    Q -. "multi-hop / relation intent" .-> GRAPH["graph-enhanced evidence expansion"]
    GRAPH --> RERANK
```

检索默认只召回 child chunks，避免 parent 与 child 在同一候选池竞争。最终结果通过 `parent_chunk_id` 装配 parent context，并在 metadata 中保留 dense、BM25、fused、rerank、graph boost 和模型审计字段。

图谱增强检索不会替代文本证据。它先从命中文本块找到相关概念和关系，再把关系证据 chunk 合并回候选集，最后统一精排和引用。

### Small-to-Big 检索

主检索路径只让 child chunks 进入召回和精排，最终再装配 parent context：

```text
child dense recall + child BM25 recall
-> weighted fusion
-> rerank
-> load parent_chunk_id
-> child evidence + parent context + citations
```

这种结构避免大块召回过粗，也避免小块缺上下文。检索结果携带 `retrieval_granularity=child_with_parent_context`、dense score、BM25 score、融合分数、精排分数、graph boost 和模型审计字段。

### 智能体问答

问答链路由可观测节点组成：

```text
问题分析 -> 路由 -> 查询改写 -> 检索 -> 证据评估 -> 上下文合成 -> 回答生成 -> 引用检查 -> 自检
```

每个节点写入 `agent_trace_events`，包括节点名、状态、输入/输出摘要、候选文档、分数、耗时和错误信息。回答必须附带真实 chunk 引用；图谱只用于增强证据候选，不生成缺少文本证据的结论。

## 技术优势

| 优势 | 体现 |
| --- | --- |
| 证据优先 | 答案、关系和图谱增强都回到真实文本块与 parent context |
| 上下文与精度兼顾 | child chunk 精确召回，parent chunk 提供完整解释上下文 |
| 图谱结构可控 | R-NN + K-NN 限制边数，连通分量和社区算法降低噪声 |
| 课程资料友好 | 保留章节、页码、公式、表格、Notebook cell 和来源类型 |
| 可审计 | 保存 batch/job/log、模型调用、检索分数、fallback 状态和引用 |
| 可恢复 | PostgreSQL 保存生命周期状态，Qdrant / Redis 可从持久记录修复 |
| 无静默降级 | 模型、数据库、Qdrant 不可用时快速失败并给出错误上下文 |
| 可扩展 | 精排、语义切分、图谱增强和模型端点通过配置与服务层隔离 |

## 数据模型

```mermaid
erDiagram
    Course ||--o{ Document : has
    Document ||--o{ DocumentVersion : versions
    DocumentVersion ||--o{ Chunk : chunks
    Chunk ||--o{ Chunk : children
    Course ||--o{ Concept : has
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source
    Concept ||--o{ ConceptRelation : target
    Course ||--o{ IngestionBatch : batches
    IngestionBatch ||--o{ IngestionJob : jobs
    Course ||--o{ QASession : sessions
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

| 表 | 作用 |
| --- | --- |
| `courses` | 课程工作区 |
| `documents` / `document_versions` | 文件元数据、版本和解析产物路径 |
| `chunks` | 父子文本块、摘要、关键词、embedding text version 和证据文本 |
| `concepts` | 概念、章节引用、证据数、社区、中心性和图谱排序 |
| `concept_aliases` | 概念别名和规范化别名 |
| `concept_relations` | 稀疏边、关系类型、证据 chunk、权重、语义相似度、支持数和推断来源 |
| `ingestion_batches` / `ingestion_jobs` | 批量导入与单文件任务 |
| `ingestion_logs` / `ingestion_compensation_logs` | 事件日志与跨存储补偿记录 |
| `qa_sessions` / `agent_runs` / `agent_trace_events` | 问答会话、智能体运行和可观测轨迹 |

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
| `ENABLE_DATABASE_FALLBACK` | 数据库降级开关，默认 `false` |
| `QDRANT_URL` / `QDRANT_COLLECTION` | Qdrant 地址和集合名 |
| `REDIS_URL` | Redis 地址 |
| `COURSE_NAME` | 默认课程名 |
| `DATA_ROOT` | 本地数据根目录 |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI 兼容模型接口 |
| `OPENAI_RESOLVE_IP` | 需要固定解析模型域名时使用的目标 IP |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` / `EMBEDDING_BATCH_SIZE` | 向量模型、维度和批大小 |
| `CHAT_MODEL` | 对话与图谱抽取模型 |
| `GRAPH_EXTRACTION_CHUNK_LIMIT` / `GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT` | 图谱抽取 chunk 上限和单文档采样上限 |
| `ENABLE_MODEL_FALLBACK` | 模型降级开关，默认 `false` |
| `RERANKER_ENABLED` / `RERANKER_MODEL` / `RERANKER_MAX_LENGTH` | Cross-Encoder 精排配置 |
| `SEMANTIC_CHUNKING_ENABLED` / `SEMANTIC_CHUNKING_MIN_LENGTH` | 语义切分开关和最小文本长度 |
| `MODEL_BRIDGE_ENABLED` / `MODEL_BRIDGE_PORT` | 宿主机模型桥接开关和端口 |

Docker Compose 会在 API 容器内使用服务名覆盖基础设施地址：

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/course_kg
QDRANT_URL=http://qdrant:6333
REDIS_URL=redis://redis:6379/0
```

如果宿主机可以访问模型供应商，但容器内到模型端点的网络不稳定，可以启用模型桥接。模型桥接只转发真实 OpenAI 兼容接口，不生成假响应，也不是 fallback。

## 运行

1. 配置 `.env`，至少提供真实模型接口：

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

2. 启动 Docker 栈：

```powershell
docker compose -f infra/docker-compose.yml up -d api web postgres redis qdrant
```

Windows 用户也可以直接双击 `start-app.bat` 一键启动后端、前端和基础设施容器；该脚本**不会**强制重建镜像，适合日常快速启动。

如果应用代码或依赖发生变更，需要重新构建本地镜像，可运行：

```powershell
docker compose -f infra/docker-compose.yml build api web
```

或在 Windows 下直接运行 `rebuild-images.bat`；如需强制无缓存重建，可执行 `rebuild-images.bat -NoCache`。

3. 打开 Web：

```text
http://127.0.0.1:3000
```

## 验证

后端单元测试：

```powershell
docker exec course-kg-api python -m pytest tests
```

前端检查：

```powershell
npm run typecheck --workspace web
npm run lint --workspace web
npm run test --workspace web
```

Docker smoke：

```powershell
python scripts/docker_smoke.py --base-url http://127.0.0.1:8000/api
```

课程质量门禁：

```powershell
docker exec course-kg-api python /app/scripts/quality_gate.py --course-name "课程名称"
```

重新解析单门课程并清理旧派生数据：

```powershell
docker exec course-kg-api python /app/scripts/reingest_all_courses.py --course-name "课程名称" --cleanup-stale
```

验收重点：

| 检查项 | 期望 |
| --- | --- |
| 健康状态 | `/api/health` 返回可用服务状态 |
| 运行时配置 | `/api/settings/runtime-check` 没有阻断项 |
| 模型降级 | `ENABLE_MODEL_FALLBACK=false`，模型不可用时快速失败 |
| 数据库降级 | `ENABLE_DATABASE_FALLBACK=false`，数据库不可用时快速失败 |
| 向量健康 | Qdrant 向量数量与 active chunks 对齐，没有零向量 |
| 检索质量 | child recall、parent context、rerank 和 citations 字段完整 |
| 图谱质量 | 节点数达到保留下限，边数近线性增长，社区字段、中心性和权重非空 |
| 日志可观测 | ingestion logs 暴露进度、retry、失败原因和 terminal event |

## 版本库规则

不进入版本库：

- `.env`、本地密钥、Authorization header 或 provider 响应。
- `data/`、`output/`、`models/`、`comparative_experiment/` 运行数据。
- `node_modules/`、`.next/`、`dist/`、`build/`、coverage、Playwright 报告。
- `.db`、`.sqlite*`、`__pycache__/`、`*.pyc`、`*.tsbuildinfo`、日志和临时文件。

应进入版本库：

- `apps/api`、`apps/web`、`packages/shared`、`scripts`、`infra`。
- README、`.env.example`、Docker 配置、测试、schema 和类型契约。
