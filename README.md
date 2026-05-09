[English](./README.en.md) | **中文**

<p align="center">
  <img src="./assets/diagraph-logo.svg" alt="DialoGraph 标志" width="132" height="132">
</p>

<h1 align="center">DialoGraph</h1>

DialoGraph 是面向本地课程资料的 Docker 化知识基础设施。系统把 PDF、课件、文档、网页、Notebook、图片和 Markdown 解析为可检索文本块、Qdrant 向量、PostgreSQL 稀疏知识图谱和带引用问答结果。

默认运行依赖真实 PostgreSQL、Qdrant、Redis 和 OpenAI 兼容模型接口。模型 fallback 与数据库 fallback 默认关闭；生产质量验证不使用零向量、fake embedding、本地 JSON 检索或抽取式替代回答。

## 快速概览

| 维度       | 实现                                                                                                              |
| ---------- | ----------------------------------------------------------------------------------------------------------------- |
| 运行方式   | Docker Compose，全栈容器化                                                                                        |
| 后端       | FastAPI、Pydantic、SQLAlchemy、NetworkX、LangGraph                                                                |
| 前端       | Next.js 16.2.4、React 19、TypeScript、TanStack Query、ECharts                                                     |
| 数据库     | PostgreSQL 16，保存课程、文件版本、文本块、图谱、问答会话和运行轨迹                                               |
| 向量库     | Qdrant 1.17.1，集合 `knowledge_chunks`                                                                          |
| 缓存与协调 | Redis 7                                                                                                           |
| 模型接口   | OpenAI 兼容 Embedding / Chat API                                                                                  |
| 检索       | 分层检索：Query-Type 感知的 Fast / Standard / Deep Graph 三层召回，Redis 缓存，融合、精排，再装配 parent context  |
| 图谱       | LLM 抽候选，chunk 向量建语义图，图算法做构图、消冗、社区、中心性和隐式关系；支持增量更新与全量重建                |
| 问答       | Agentic RAG：Perception → Planning → Retrieval → EvidenceEvaluator → Generation，支持跨语言检索与前置证据评估 |

## 技术栈

| 层         | 技术                                                               | 职责                                                            |
| ---------- | ------------------------------------------------------------------ | --------------------------------------------------------------- |
| 前端       | Next.js 16.2.4、React 19、TypeScript、TanStack Query、ECharts      | 课程管理、上传解析、搜索、问答、图谱浏览、运行时配置            |
| API        | FastAPI、Pydantic、SQLAlchemy、LangGraph                           | REST / SSE 接口、强类型校验、事务编排、导入任务、检索与问答编排 |
| 图算法     | NetworkX、NumPy、SciPy                                             | 稀疏构图、连通分量、Louvain、谱聚类、中心性、Dijkstra 隐式关系  |
| 数据库     | PostgreSQL 16                                                      | 课程、文件版本、chunks、图谱、问答会话、运行轨迹和补偿记录      |
| 向量检索   | Qdrant 1.17.1                                                      | parent / child chunk 向量、dense recall、向量健康检查           |
| 词面检索   | PostgreSQL 文本数据、BM25                                          | child chunk 词面召回与混合检索融合                              |
| 缓存与协调 | Redis 7                                                            | 运行态缓存、任务协调、服务依赖                                  |
| 解析       | PyMuPDF、PPTX / DOCX / Markdown / HTML / Notebook 解析器、OCR 路径 | 把异构课程资料转成结构化章节与文本                              |
| 模型接口   | OpenAI 兼容 Embedding / Chat API                                   | 向量化、摘要、关键词、实体候选、关系候选、回答生成              |
| 精排       | 轻量精排、可选 Cross-Encoder                                       | 对融合候选按相关性重排                                          |
| 部署       | Docker Compose                                                     | 固定服务边界、依赖版本和本地持久化                              |
| 测试       | pytest、Vitest、Next build、Docker smoke                           | 行为回归、前后端类型契约、无 fallback 质量门禁                  |

## 核心能力

| 能力           | 说明                                                                                |
| -------------- | ----------------------------------------------------------------------------------- |
| 多格式解析     | 支持 PDF、PPT/PPTX、DOCX、Markdown、TXT、Notebook、HTML 和图片资料                  |
| 父子块切分     | parent chunk 保存完整上下文，child chunk 承担精确召回、精排和证据引用               |
| 语义切分       | 对长文本按结构、语义边界、句子边界和长度上限切分，可用 embedding 相似度辅助边界判断 |
| 上下文增强向量 | 向量输入包含文件元数据、章节、父块摘要、相邻子块摘要、关键词、表格和公式标记        |
| 混合检索       | Qdrant child dense recall 与 child BM25 recall 融合，统一进入精排                   |
| 跨语言检索     | LLM 翻译查询扩展至双语子查询，DocumentGrader 用 embedding similarity 跨越语言壁垒   |
| 图谱增强       | 图谱关系必须回到证据 chunk，作为检索扩展信号而不是替代证据                          |
| 图论构图       | 用稀疏图、社区、中心性、Dijkstra 和关系补全降低噪声、保留关键结构                   |
| 可观测问答     | 保存检索审计、模型调用审计、agent trace、引用和失败原因                             |
| 运行时检查     | 暴露健康检查、runtime-check、fallback 状态、Qdrant 状态和模型端点状态               |

## 系统架构

```mermaid
flowchart TB
    USER["用户浏览器"] --> WEB["Next.js Web<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["FastAPI<br/>course-kg-api"]

    subgraph APP["应用层"]
        API --> INGEST["导入流水线<br/>解析 -> 父子切块 -> 知识增强 -> 向量写入"]
        API --> RETRIEVAL["检索流水线<br/>dense/BM25 -> 融合 -> 精排 -> parent context"]
        API --> GRAPH["图谱流水线<br/>LLM候选 -> 向量相似图 -> 稀疏构图 -> 社区/中心性/推断"]
        API --> QA["Agentic 问答<br/>Perception -> Planning -> Retrieval -> EvidenceEvaluator -> Generation"]
    end

    subgraph STORE["存储与运行态"]
        PG[("PostgreSQL<br/>元数据、文本块、稀疏图、审计记录")]
        QD[("Qdrant<br/>chunk 向量与相似召回")]
        RD[("Redis<br/>运行态缓存、embedding / 检索结果缓存、任务协调")]
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
    API->>DB: 增量更新：仅变更文档关联的图谱局部重算
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

> **设计意图（为什么这么做）**：普通的固定长度切块会导致严重的内容割裂（断章取义）。采用父子分层与语义切分，使得模型在检索阶段能利用子块的高精确度，而在生成阶段又能获取父块的完整上下文，彻底解耦了“召回单元”与“生成单元”。

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

> **设计意图（为什么这么做）**：子块由于文本较短，在独立转换为向量时极易产生语义歧义（例如单独的“该方法”、“这一步”）。在 Embedding 前强制注入父块摘要和相邻上下文，相当于在入库前对文本做了“环境灌注”（Contextual Retrieval），极大提升了 Dense Recall 阶段的召回准确率。

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

> **设计意图（为什么这么做）**：传统的 GraphRAG 直接让 LLM 对提取出的概念名进行向量化，这会导致向量空间偏向大模型预训练数据的通用语境。使用支撑该概念的所有底层证据向量（Child Chunks）求质心，确保了图谱在向量空间中完美忠实于本地课程的特定语境，从根源上消除概念漂移。

### 2. 动态 R-NN + K-NN 稀疏构图

每个概念按证据量动态决定发出候选边数量：

$$
K_i = \mathrm{clamp}\bigl(4 + \lfloor \log_2(1 + m_i) \rfloor,\, 4,\, 12\bigr)
$$

每个概念按章节覆盖动态限制接收反向候选：

$$
R_i = \mathrm{clamp}\bigl(2 + \lfloor \log_2(1 + r_i) \rfloor,\, 2,\, 8\bigr)
$$

$m_i$ 是证据 chunk 数，$r_i$ 是章节引用数。系统保留互为近邻、通过反向接收限制的近邻，以及高置信 LLM 显式关系，从而让边数随节点数近线性增长。

> **设计意图（为什么这么做）**：如果毫无限制地让高频词（如“算法”、“数据”）接收连边，整个图谱会迅速坍塌成一个毫无意义的巨大枢纽节点（Hubness Problem）。基于证据量与章节覆盖率的动态收发限制算法，强制挤掉蹭热度的低质边，从数学上保证了图谱永远是清晰、稀疏且重点突出的。

### 3. 边权与图算法

边权由 LLM 置信度、语义相似度、证据支持和结构一致性组合：

$$
w_{ij}=0.45\,c_{ij}^{\mathrm{llm}}+0.30\,s_{ij}^{\mathrm{sem}}+0.15\,s_{ij}^{\mathrm{evidence}}+0.10\,s_{ij}^{\mathrm{structure}}
$$

无 LLM 显式关系时 $c_{ij}^{\mathrm{llm}}=0$，最终 $w_{ij}$ 裁剪到 $[0,1]$。图算法阶段执行：

- 连通分量消融：移除孤立、低证据、低重要度噪声节点，同时保持每门课程有足够节点规模。
- Louvain 社区发现：用于主社区标记和前端颜色分组。
- 谱聚类：用于大连通分量和大社区的二级结构划分。
- 中心性：计算 degree、weighted degree、PageRank、betweenness、closeness，并合成 `centrality_score`。
- 图谱精简：优先保留高中心性节点、社区代表节点、跨社区桥接边和高证据节点。

> **设计意图（为什么这么做）**：大模型抽取的图谱通常充满噪音和孤立碎片。引入连通分量消融、Louvain 社区发现和中心性计算等经典的传统图论算法，是对冲 LLM 随机性幻觉的最有效手段。通过多维图论消融，只保留高价值核心结构，解决了大规模建图时的“毛线球（Hairball）”展示难题。

### 4. 隐式关系与关系补全

Dijkstra 在非负代价图上发现 2-3 跳隐式关系：

$$
\mathrm{cost}_{ij}=\frac{1}{0.05+w_{ij}}
$$

当端点语义相似度足够高且路径代价足够低时，系统写入 `relation_source="dijkstra_inferred"` 的 `relates_to` 边，并用路径分数修正弱边权。随后系统对高中心性节点的二跳邻域抽取证据 snippet，让 LLM 只基于给定证据做抽取式关系补全。

前端图展示按 Louvain 社区着色，节点大小反映中心性和图谱排序，虚线边表示 inferred 关系。用户可以筛选社区并快速打开关键实体详情。

> **设计意图（为什么这么做）**：真正的知识往往跨越章节（文档没有明说 A 和 C 的关系，但 A 属于 B，B 包含 C）。用 Dijkstra 去发现隐藏的结构洞（高效），再用 LLM 针对 2 跳证据片段做抽取式验证（精准），实现了一种自动化的本体扩展（Ontology Expansion），突破了传统正则/规则抽取的瓶颈。

## 检索与问答

DialoGraph 的问答链路采用 **Perception → Planning → Retrieval → EvidenceEvaluator → Generation** 五阶段 Agent 架构，以 LangGraph 编排。所有节点写入 `agent_trace_events`，前端通过 SSE 实时展示运行轨迹。

```mermaid
flowchart LR
    Q["问题"] --> PER["Perception<br/>意图识别 · 实体抽取 · 图概念匹配"]
    PER -->|"greeting / clarify"| AG["AnswerGenerator"]
    PER -->|"需要检索"| PLAN["RetrievalPlanner<br/>策略选择 · 跨语言翻译 · 查询扩展"]
    PLAN --> RET["RetrievalExecutor<br/>global_dense / local_graph / hybrid / community"]
    RET --> GRADE["DocumentGrader<br/>0.4·overlap + 0.6·embedding_sim"]
    GRADE --> EVAL["EvidenceEvaluator<br/>前置证据充分性评估"]
    EVAL -->|"不充分 + retry<2"| PLAN
    EVAL -->|"充分 / 不充分+retry≥2"| CS["ContextSynthesizer"]
    CS --> AG
    AG --> CC["CitationChecker"]
    CC --> CV["CitationVerifier"]
    CV --> REFL["Reflection<br/>后生成反思（默认关闭）"]
    REFL --> AC["AnswerCorrector"]
    AC --> CS
```

### Perception（感知层）

Perception 节点负责理解用户意图、抽取实体并在课程图谱中匹配相关概念：

1. **Fast-path**：问候语直接路由到 `direct_answer`；空查询或指代消解路由到 `clarify`。
2. **LLM 感知**：调用 ChatProvider 分析意图（`definition` / `comparison` / `analysis` / `application` / `procedure` 等），抽取实体列表和子查询。
3. **图概念匹配**：将抽取的实体与 `concepts` 表和 `concept_aliases` 表做规范化匹配，获取匹配概念的社区标签和一阶邻居关系。

Perception 输出包含：

- `intent`：问题类型
- `entities` / `matched_concepts`：提取实体与图谱匹配结果
- `perceived_communities`：相关社区 ID 集合
- `suggested_strategy`：建议检索策略（`global_dense`、`local_graph`、`hybrid`、`community`）
- `needs_graph`：是否需要图谱增强

### RetrievalPlanner（规划层）

规划层根据 Perception 输出选择检索策略，并执行跨语言查询翻译：

**策略选择规则：**

| 意图                                   | 条件                  | 策略                                |
| -------------------------------------- | --------------------- | ----------------------------------- |
| `definition`                         | `needs_graph=false` | `global_dense`（纯 dense + BM25） |
| `comparison` 或 `needs_graph=true` | —                    | `hybrid`（分层混合检索）          |
| `application` / `procedure`        | 有匹配概念            | `local_graph`（局部图搜索）       |
| `analysis`                           | 匹配概念 ≥3          | `community`（社区范围搜索）       |

**跨语言翻译扩展**：

系统检测查询语言（中/英），通过 LLM 翻译为对侧语言：

$$
Q_{\mathrm{bilingual}} = \{q_{\mathrm{original}},\; q_{\mathrm{translated}}\} \cup Q_{\mathrm{sub}}
$$

子查询去重后全部进入 RetrievalExecutor。这让中文查询（如"最大流"）也能通过英文翻译（"max flow"）命中英文课程材料中的 BM25 词面匹配。

> **设计意图（为什么这么做）**：多语言 Embedding 模型在跨语言对齐上常常表现不佳。通过显式的查询翻译并入双语子查询，使得后续检索可以通过多种语言形态并行探测文档库，这是比单纯依赖 Embedding 模型底层对齐更鲁棒的工程解法。

### RetrievalExecutor（检索执行层）

执行层根据策略分发到不同检索后端：

| 策略               | 后端                                                  | 说明                                                                                                                 |
| ------------------ | ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `global_dense`   | `hybrid_search_chunks`                              | 纯 dense + BM25 混合召回                                                                                             |
| `local_graph`    | `local_graph_search`                                | 以 Perception 匹配概念为种子，召回种子概念及其 1-hop 邻居的证据 chunk，再与 base dense recall 合并去重               |
| `community`      | `community_search_chunks`                           | 限定在感知到的 Louvain 社区内，对社区概念关联的 evidence chunk 做 dense 召回融合，社区内 chunk 额外 +0.15 分数 boost |
| `hybrid`（默认） | `layered_search_chunks` / `graph_enhanced_search` | 按 Query Type 分层路由：Layer 1 Fast / Layer 2 Standard / Layer 3 Deep Graph                                         |

所有策略都遵循 **Small-to-Big** 原则：只召回 child chunks，避免 parent/child 竞争，最终通过 `parent_chunk_id` 装配 parent context。

### DocumentGrader（文档评分层）

对召回文档做准入评分，融合词面重叠与向量语义相似度：

$$
\mathrm{grade\_score} = 0.40 \cdot r_{\mathrm{overlap}} + 0.60 \cdot s_{\mathrm{embedding}}
$$

其中：

- $r_{\mathrm{overlap}} = \dfrac{|T_q \cap T_d|}{|T_q|}$，$T_q$ 为查询词集合，$T_d$ 为文档标题+摘要+正文词集合
- $s_{\mathrm{embedding}}$ 为查询向量与文档向量的余弦相似度；当文档未携带原始向量时，回退到检索阶段记录的 dense score

准入规则（满足任一即可通过）：

$$
\begin{cases}
\mathrm{grade\_score} \ge 0.35 & \text{（主通道）} \\
s_{\mathrm{embedding}} \ge 0.45 & \text{（跨语言桥接通道）} \\
r_{\mathrm{overlap}} \ge 0.25 \;\land\; \mathrm{original\_score} \ge 0.3 & \text{（辅助通道）}
\end{cases}
$$

跨语言桥接通道解决了一个关键问题：中文查询"最大流"与英文材料"max flow"在 `text-embedding-v4` 的向量空间中重叠较弱，但 LLM 翻译后的子查询通过 dense recall 能召回相关 chunk。此时 $r_{\mathrm{overlap}}$ 可能接近 0，但 $s_{\mathrm{embedding}}$ 仍然较高，桥接通道防止这类有效跨语言结果被词面匹配误杀。

> **设计意图（为什么这么做）**：这是一个专为“跨语言墙”设计的破局漏斗。中文查询和英文材料往往字面毫无交集（overlap=0），但高维语义极高。通过设立基于 $s_{\mathrm{embedding}} \ge 0.45$ 的跨语言桥接豁免通道，巧妙修补了纯词面匹配（BM25）在面对翻译差异时的严重误杀问题。

### EvidenceEvaluator（前置证据评估层）

**在生成答案之前**评估已检索证据是否充分：

对每份 graded document 提取 `grade_score`，计算：

$$
\bar{g} = \frac{1}{n}\sum_{i=1}^{n} g_i,\qquad g_{\max} = \max_i g_i
$$

按意图设定最低证据阈值：

$$
\begin{cases}
(n_{\min}, \bar{g}_{\min}) = (1,\, 0.25) & \text{if intent} \in \{\text{definition},\, \text{procedure}\} \\
(n_{\min}, \bar{g}_{\min}) = (2,\, 0.20) & \text{if intent} \in \{\text{comparison},\, \text{analysis}\} \\
(n_{\min}, \bar{g}_{\min}) = (1,\, 0.20) & \text{otherwise}
\end{cases}
$$

充分性判定：

$$
\mathrm{sufficient} \;\Leftrightarrow\; g_{\max} \ge 0.35 \;\land\; n \ge n_{\min} \;\land\; \bar{g} \ge \bar{g}_{\min}
$$

若仅有锚点但数量/分数边缘，则标记为 `marginal` 仍允许生成。若证据不充分且 `retry_count < 2`，路由回 `RetrievalPlanner`，`top_k` 翻倍后重新检索；若 `retry_count ≥ 2`，标记 `low_evidence=true` 并进入生成，生成器会在提示中注入免责声明，且不强制引用可能不相关的 chunk。

> **设计意图（为什么这么做）**：这是打破传统 RAG“无论搜出什么垃圾都强行生成”的关键。作为防御性评估层，它赋予了系统“知道自己不知道”的能力。与其放任 LLM 胡编乱造，不如主动拦截低质检索并触发降级免责，这对于专业知识问答的可靠性至关重要。

### 后生成闭环（默认关闭）

`ENABLE_POST_GENERATION_REFLECTION=false`（默认）。后生成节点在启用时执行：

- **CitationVerifier**：对答案中的高重要性陈述抽样做 NLI 验证。
- **Reflection**：LLM 评估答案是否存在幻觉、覆盖不足或矛盾，返回 `has_issue` / `issue_type` / `suggestion`。
- **AnswerCorrector**：根据反思结果调整策略（扩大 top_k、改写 query 或保留高置信文档重新生成）。

这些节点在 trace 中可观测，但默认不参与主链路，避免增加延迟和模型调用成本。前置 `EvidenceEvaluator` 已覆盖大多数证据不足场景。

### 分层检索

系统根据 Query Type 和路由自动选择检索深度：

| 层级               | 触发条件                                | 说明                                                                                    |
| ------------------ | --------------------------------------- | --------------------------------------------------------------------------------------- |
| Layer 1 Fast       | `definition` / `formula` 类简单查询 | Redis 缓存优先，仅 dense recall，跳过 BM25                                              |
| Layer 2 Standard   | `example` / `procedure` / default   | dense + BM25 hybrid，融合后精排                                                         |
| Layer 3 Deep Graph | `comparison` / `multi-hop`          | hybrid + 图谱 v2 增强：centrality boost、community aggregation、Dijkstra path expansion |

embedding 向量与检索结果写入 Redis，TTL 绑定 embedding text version 和课程最新 document version。

### Small-to-Big 检索

主检索路径只让 child chunks 进入召回和精排，最终再装配 parent context：

```text
child dense recall + child BM25 recall
-> weighted fusion
-> rerank
-> load parent_chunk_id
-> child evidence + parent context + citations
```

检索结果携带 `retrieval_granularity=child_with_parent_context`、dense score、BM25 score、融合分数、精排分数、graph boost 和模型审计字段。

## 技术优势

| 优势             | 体现                                                                                               |
| ---------------- | -------------------------------------------------------------------------------------------------- |
| 证据优先         | 答案、关系和图谱增强都回到真实文本块与 parent context                                              |
| 上下文与精度兼顾 | child chunk 精确召回，parent chunk 提供完整解释上下文                                              |
| 图谱结构可控     | R-NN + K-NN 限制边数，连通分量和社区算法降低噪声                                                   |
| 课程资料友好     | 保留章节、页码、公式、表格、Notebook cell 和来源类型                                               |
| 可审计           | 保存 batch/job/log、模型调用、检索分数、fallback 状态和引用                                        |
| 可恢复           | PostgreSQL 保存生命周期状态，Qdrant / Redis 可从持久记录修复                                       |
| 无静默降级       | 模型、数据库、Qdrant 不可用时快速失败并给出错误上下文                                              |
| 可扩展           | 精排、语义切分、图谱增强和模型端点通过配置与服务层隔离                                             |
| Agent 架构清晰   | Perception-Planning-Retrieval-EvidenceEvaluator-Generation 五阶段分离，每层可独立观测与调优        |
| 跨语言鲁棒       | LLM 翻译查询扩展 + embedding similarity 桥接 + 跨语言准入通道，缓解单语言 embedding 模型的对齐缺陷 |

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

| 表                                                        | 作用                                                             |
| --------------------------------------------------------- | ---------------------------------------------------------------- |
| `courses`                                               | 课程工作区                                                       |
| `documents` / `document_versions`                     | 文件元数据、版本和解析产物路径                                   |
| `chunks`                                                | 父子文本块、摘要、关键词、embedding text version 和证据文本      |
| `concepts`                                              | 概念、章节引用、证据数、社区、中心性和图谱排序                   |
| `concept_aliases`                                       | 概念别名和规范化别名                                             |
| `concept_relations`                                     | 稀疏边、关系类型、证据 chunk、权重、语义相似度、支持数和推断来源 |
| `ingestion_batches` / `ingestion_jobs`                | 批量导入与单文件任务                                             |
| `ingestion_logs` / `ingestion_compensation_logs`      | 事件日志与跨存储补偿记录                                         |
| `qa_sessions` / `agent_runs` / `agent_trace_events` | 问答会话、智能体运行和可观测轨迹                                 |

## 配置

复制配置模板：

```powershell
Copy-Item .env.example .env
```

常用配置：

| 变量                                                                        | 说明                                                                          |
| --------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `API_HOST_PORT` / `WEB_HOST_PORT`                                       | 宿主机访问端口                                                                |
| `DATABASE_URL`                                                            | PostgreSQL 连接地址                                                           |
| `ENABLE_DATABASE_FALLBACK`                                                | 数据库降级开关，默认 `false`                                                |
| `QDRANT_URL` / `QDRANT_COLLECTION`                                      | Qdrant 地址和集合名                                                           |
| `REDIS_URL`                                                               | Redis 地址                                                                    |
| `COURSE_NAME`                                                             | 默认课程名                                                                    |
| `DATA_ROOT`                                                               | 本地数据根目录                                                                |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL`                                    | OpenAI 兼容模型接口                                                           |
| `OPENAI_RESOLVE_IP`                                                       | 需要固定解析模型域名时使用的目标 IP                                           |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` / `EMBEDDING_BATCH_SIZE`   | 向量模型、维度和批大小                                                        |
| `CHAT_MODEL`                                                              | 对话与图谱抽取模型                                                            |
| `GRAPH_EXTRACTION_CHUNK_LIMIT` / `GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT` | 图谱抽取 chunk 上限和单文档采样上限                                           |
| `ENABLE_MODEL_FALLBACK`                                                   | 模型降级开关，默认 `false`                                                  |
| `RERANKER_ENABLED` / `RERANKER_MODEL` / `RERANKER_MAX_LENGTH`         | Cross-Encoder 精排配置                                                        |
| `SEMANTIC_CHUNKING_ENABLED` / `SEMANTIC_CHUNKING_MIN_LENGTH`            | 语义切分开关和最小文本长度                                                    |
| `RETRIEVAL_LAYER_ENABLED`                                                 | 检索分层开关，默认 `true`                                                   |
| `RETRIEVAL_CACHE_TTL_SECONDS`                                             | Redis 检索缓存 TTL，默认 `300`                                              |
| `ENABLE_AGENTIC_REFLECTION`                                               | Agentic 反思与修正总开关，默认 `true`                                       |
| `ENABLE_POST_GENERATION_REFLECTION`                                       | 后生成反思开关（CitationVerifier/Reflection/AnswerCorrector），默认 `false` |
| `CITATION_VERIFICATION_SAMPLE_MAX`                                        | 每答案引用验证抽样数，默认 `3`                                              |
| `REFLECTION_MAX_RETRIES`                                                  | 反思触发修正的最大重试次数，默认 `2`                                        |
| `MODEL_BRIDGE_ENABLED` / `MODEL_BRIDGE_PORT`                            | 宿主机模型桥接开关和端口                                                      |

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

重新解析单门课程并清理过时派生数据：

```powershell
docker exec course-kg-api python /app/scripts/reingest_all_courses.py --course-name "课程名称" --cleanup-stale
```

验收重点：

| 检查项       | 期望                                                                                                                                 |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| 健康状态     | `/api/health` 返回可用服务状态                                                                                                     |
| 运行时配置   | `/api/settings/runtime-check` 没有阻断项                                                                                           |
| 模型降级     | `ENABLE_MODEL_FALLBACK=false`，模型不可用时快速失败                                                                                |
| 数据库降级   | `ENABLE_DATABASE_FALLBACK=false`，数据库不可用时快速失败                                                                           |
| 向量健康     | Qdrant 向量数量与 active chunks 对齐，没有零向量                                                                                     |
| 检索质量     | child recall、parent context、rerank 和 citations 字段完整                                                                           |
| 图谱质量     | 节点数达到保留下限，边数近线性增长，社区字段、中心性和权重非空；增量更新后图谱结构稳定                                               |
| 检索分层     | 不同 query type 命中对应 layer，Redis 缓存命中/失效行为正确                                                                          |
| Agentic 闭环 | Perception、RetrievalPlanner、EvidenceEvaluator 节点在 trace 中可观测；后生成 Reflection 默认关闭；无 fallback 时 LLM 错误不静默吞掉 |
| 跨语言检索   | 中英混合查询能命中对侧语言材料，DocumentGrader 桥接通道生效                                                                          |
| 日志可观测   | ingestion logs 暴露进度、retry、失败原因和 terminal event                                                                            |

## 版本库规则

不进入版本库：

- `.env`、本地密钥、Authorization header 或 provider 响应。
- `data/`、`output/`、`models/`、`comparative_experiment/` 运行数据。
- `node_modules/`、`.next/`、`dist/`、`build/`、coverage、Playwright 报告。
- `.db`、`.sqlite*`、`__pycache__/`、`*.pyc`、`*.tsbuildinfo`、日志和临时文件。

应进入版本库：

- `apps/api`、`apps/web`、`packages/shared`、`scripts`、`infra`。
- README、`.env.example`、Docker 配置、测试、schema 和类型契约。
