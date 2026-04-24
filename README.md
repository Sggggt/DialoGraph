# Course Knowledge Base

一个把任意课程资料转成可检索知识库的本地系统。项目支持导入 `PDF`、`PPT/PPTX`、`DOCX`、`Markdown`、`TXT`、`Notebook` 和图片 OCR 内容，并将它们组织成：

- 结构化文档与版本
- 可检索文本分块
- 向量索引
- 概念与关系图谱
- 带引用的搜索与问答工作台

项目不绑定某一门固定课程。每门课程都会有独立的数据目录、知识图谱、检索结果和问答会话。

## 架构

```mermaid
flowchart LR
    U["User"] --> W["Next.js Web"]
    W --> A["FastAPI API"]
    A --> DB[("PostgreSQL / SQLite fallback")]
    A --> VS[("Qdrant / JSON fallback")]
    A --> FS[("Local course storage")]
    A --> LLM["DashScope / Qwen"]
    WK["Celery Worker / Watchdog"] --> A
```

组件划分：

- `apps/web`
  前端工作台，提供课程选择、资料导入、搜索、问答、概念浏览和图谱浏览。
- `apps/api`
  后端核心，负责解析、切块、向量化、检索、概念抽取、图谱构建和 Agent 问答。
- `apps/worker`
  复用 API 中的 ingestion 逻辑，通过 Celery 和 Watchdog 做后台导入与目录监听。
- `packages/shared`
  前后端共享的 TypeScript 数据契约。

## 数据与目录

默认情况下，每门课程的数据都放在 `data/<Course Name>/` 下：

```text
data/
└── <Course Name>/
    ├── source/      # 课程原始资料
    ├── storage/     # 上传文件和归档副本
    └── ingestion/   # 抽取结果、fallback 向量索引等中间产物
```

课程名会被用来生成对应目录；非法文件名字符会被自动清理。

## 核心流程

### 1. 导入

```text
Course files
  -> parse_document
  -> chunk_sections
  -> embed_texts
  -> vector_store.upsert
  -> upsert_concepts_from_chunk
  -> documents / versions / chunks / concepts / relations / jobs / batches
```

### 2. 检索

检索采用混合召回：

- dense retrieval
- lexical retrieval
- RRF 融合
- 基于标题、章节、内容类型的轻量重排

### 3. 问答

问答不是简单的“搜索 + prompt”，而是一条 LangGraph 工作流，主要节点包括：

- `query_analyzer`
- `router`
- `query_rewriter`
- `retrievers`
- `document_grader`
- `retry_planner`
- `context_synthesizer`
- `answer_generator`
- `citation_checker`
- `self_check`

前端可以查看课程级会话、引用和 Agent trace。

## 多课程能力

当前版本已支持：

- 课程列表接口
- 新建课程工作区
- 前端头部切换课程
- 按课程隔离的导入、图谱、搜索、问答和会话列表

后端接口仍保留 `/api/courses/current/*` 这一组主路径，但通过 `course_id` 参数决定实际访问哪门课程。

## 仓库结构

```text
.
├── apps
│   ├── api
│   ├── web
│   └── worker
├── packages
│   └── shared
├── data
├── infra
├── start-app.ps1
└── package.json
```

## 快速开始

### 1. 安装前端依赖

```bash
npm install
```

### 2. 配置环境变量

参考 [`.env.example`](./.env.example)：

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/knowledge_base
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0
COURSE_NAME=Sample Course
COURSE_SOURCE_ROOT=./data/Sample Course/source
DASHSCOPE_API_KEY=
```

### 3. 启动基础设施

```bash
docker compose -f infra/docker-compose.yml up -d
```

### 4. 启动应用

```powershell
.\start-app.ps1
```

脚本会启动 API 和 Web，并默认打开前端页面。

### 5. 可选启动 Worker

```bash
cd apps/worker
uv sync
uv run celery -A worker_app.celery_app worker --loglevel=info
uv run python -m worker_app.watcher
```

## 主要接口

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

## 当前边界

- 数据迁移仍是轻量 patch 方式，不是 Alembic
- 认证和生产级权限控制尚未补齐
- 图谱抽取质量仍以启发式 + LLM 混合方案为主
- 多课程已打通主链路，但课程管理能力还比较轻量
