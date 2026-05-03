# DialoGraph 工程改进 TODO

> 基于工程水平分析报告、ACID 事务审计报告和架构深度评审，按优先级排列的改进清单。
> 当前总评：**B+ / A-（78/100）**，目标：**A（90+/100）**

---

## P0 🔴 紧急（阻碍项目可交付性）

### 事务安全与跨存储协调

> 基于 ACID 审计报告 v2，解决 DB / 向量存储 / 文件系统 三方数据一致性问题。

#### 原子性修复

- [x] `ingest_file`: 将 `create_or_update_document` 内部的 `db.commit()`（L494）改为 `db.flush()`，推迟到函数末尾统一 commit
- [x] `ingest_file`: 在 `vector_store.delete(stale_chunk_ids)` 前记录旧向量 ID 到补偿日志，失败时可恢复
- [x] `ingest_file`: 将向量 `upsert` 移到 DB commit **之后**执行，失败时仅需重试向量写入而非回滚整个事务
- [x] `upload_file` 路由: DB 写入失败时清理已落盘的孤儿文件（`stored_path.unlink(missing_ok=True)`）
- [x] `remove_course_file`: 将文件删除（`unlink`）移到 `db.commit()` 之后，避免 DB 回滚但文件已删

#### 并发控制

- [x] 为 `ingest_file` 添加基于 `source_path` 的应用级锁（`asyncio.Lock` 字典），防止同一文件并发解析
- [x] 为 `register_uploaded_file` 添加 `SELECT ... FOR UPDATE`（PG）或等效排他查询，防止并发上传同名文件竞态
- [x] `run_batch_ingestion` / `run_uploaded_files_ingestion`: 添加批次级互斥，防止同课程并发触发多个批次
- [x] 评估 `asyncio.run()` 在 `BackgroundTasks` 中的使用，考虑改为 `asyncio.create_task()` 以复用事件循环

#### 锁机制增强

- [x] `FallbackVectorStore`: 在 Windows 上用 `msvcrt.locking` 或 `portalocker` 替代纯线程锁，支持多进程安全
- [x] `FallbackVectorStore._write`: Windows 上 `Path.replace()` 非原子，添加 `os.fsync()` 确保数据落盘
- [x] 添加锁清理机制：为 `_VECTOR_FILE_LOCKS` 添加 LRU 淘汰或 WeakValueDictionary，避免长期运行泄漏

#### 跨存储一致性

- [x] 设计并实现 `IngestionCompensationLog` 表，记录向量操作（delete/upsert），崩溃恢复时重放或回滚
- [x] `ingest_file` 失败路径: 添加向量存储补偿清理（删除已写入的新向量、恢复已删除的旧向量）
- [ ] 在 `finalize_interrupted_batches()` 中增加向量存储一致性检查：对比 DB 中活跃 chunk ID 与向量存储中的 ID
- [ ] `rebuild_course_graph` 前添加向量存储健康检查，确认向量数据可用后再删除旧图

#### Agent 事务一致性

- [ ] `run_agent`: 在 `append_session_turn` 前用 `try/finally` 确保 transcript 一定被写入，即使后续操作失败
- [ ] 文档化 Agent 多次 commit 是有意的可观测性设计决策（在代码注释和 README 中说明）

### GraphRAG：图谱增强检索

> 当前向量检索和知识图谱完全独立，图谱关系未被用于增强检索。
> 以下任务实现「向量召回 → 图谱扩展 → 轻量精排」管线。

#### 核心管线

- [x] 在 `retrieval.py` 中新增 `graph_enhanced_search()` 函数，实现以下流程：
  - [x] **Step 1**：调用现有 `hybrid_search_chunks()` 得到 top-K chunks
  - [x] **Step 2**：从 top-K chunks 提取关联的 concept_ids（通过 `ConceptRelation.evidence_chunk_id` 反查）
  - [x] **Step 3**：沿 `concept_relations` 扩展 1 跳相邻概念（`source_concept_id` ↔ `target_concept_id`）
  - [x] **Step 4**：通过相邻概念的 `evidence_chunk_id` 找到关联 chunks
  - [x] **Step 5**：将图谱扩展的 chunks 合并到原始结果中，用 `confidence * importance_score` 加权后重新排序
- [x] 在 `agent_graph.py` 的 `HybridRetriever` 中，当 `route == "multi_hop_research"` 时调用 `graph_enhanced_search()`

#### 概念向量去重

- [ ] 为已抽取的概念生成 embedding（概念名 + summary），存入独立向量集合
- [ ] 在 `upsert_concepts_from_chunk()` 中，对新概念做 embedding 相似度匹配（cosine > 0.92 自动合并）
- [ ] 处理缩写与全称的消歧（如 "BFS" ↔ "Breadth-First Search"）

#### 图谱质量增强

- [ ] 用 chunk embedding 共现频率推断隐式 `co_occurs_with` 关系
- [ ] 用概念关联 chunks 的被检索频次校准 `importance_score`
- [ ] Router 升级为 LLM 分类器，替代关键词匹配路由

### 测试基础设施

- [x] 搭建 Python 测试框架（pytest + pytest-asyncio + httpx TestClient）
- [x] 为 `parsers.py` 编写单元测试（覆盖 PDF / Markdown / Notebook / PPTX / DOCX / HTML 解析）
- [x] 为 `chunking.py` 编写单元测试（切块大小、重叠、content_kind 推断）
- [x] 为 `retrieval.py` 编写单元测试（lexical_search、hybrid_search、RRF 融合逻辑）
- [x] 为 `ingestion.py` 核心流程编写集成测试（ingest_file 端到端）
- [x] 为 `agent_graph.py` 编写测试（路由判断、查询重写、文档评分逻辑）
- [x] 为 `concept_graph.py` 编写测试（概念提取、名称规范化、图谱合并）
- [ ] 为 `embeddings.py` 编写测试（fallback 逻辑、fake embedding 生成）
- [x] 搭建前端测试框架（Vitest + Testing Library）
- [x] 为 `lib/api.ts` 编写测试（API 客户端、SSE 流解析）
- [x] 添加 `conftest.py`，提供测试用的 SQLite 内存数据库 fixture

### 安全加固

- [x] 添加 API 认证中间件（API Key 或 JWT Bearer Token）
- [x] 收紧 CORS 配置（替换 `allow_origins=["*"]` 为白名单）
- [x] 为 `ensure_schema()` 中的 DDL 操作消除字符串拼接，改用参数化或 SQLAlchemy DDL API
- [x] 对 `top_k` 等用户输入参数添加上限约束（如 `Field(le=50)`）

### 外部服务稳定性与数据质量

> 基于架构评审报告，解决 model-bridge 网络连通性和历史零向量问题。

- [x] 修复 model-bridge：`__none__` 被传入 curl `--resolve` 导致的 502 Bad Gateway
- [x] 修复 model-bridge：`start-app.ps1` 对 `OPENAI_RESOLVE_IP` 为 `__none__` 时的过滤逻辑
- [x] 移除外部 reranker 运行时：用纯 Python `lightweight_rerank()` 替代 CrossEncoder 容器（~2GB 镜像移除）
- [x] 清理环境变量：从 `.env` / `.env.example` / `config.py` / `schemas.py` 中移除所有 `RERANKER_*` 配置
- [x] 清理前端 UI：从 Settings 页面移除 reranker 开关、模型输入、设备选择器和运行时状态检查
- [x] 更新项目文档：README（双语版）移除 reranker 架构图节点、构建说明和模型缓存章节
- [ ] 为 model-bridge 添加代理感知能力（读取 `HTTP_PROXY`/`HTTPS_PROXY` 或 `.env` 代理配置）
- [ ] 添加零向量监控：ingestion 完成后抽样检查 qdrant 向量非零率，发现异常立即告警

### 工业级上下文管理

> 当前 QA 的上下文管理处于基础版：前端不传递 history，后端硬编码取 `[-8:]` 条，无 Token 预算、无压缩策略、无可配置项。以下任务将上下文管理提升至工业级。

#### 前后端历史同步

- [ ] 前端 `qa-workspace.tsx`：发送请求时显式携带 `history`（从 `turns` 组装 `ChatMessage[]`）
- [ ] 后端 `create_agent_run_context()`：优先使用请求中的 `history`，与 `session.transcript` 做合并校验（防篡改/防丢失）
- [ ] 后端：当前端 `history` 与 `transcript` 不一致时，以数据库为准并打日志警告

#### Token 预算与截断策略

- [ ] 引入 tokenizer（`tiktoken` 或模型自带 tokenizer）计算对话历史 Token 数
- [ ] 定义 `chat_context_window` 和 `chat_history_budget_tokens` 配置项（默认留 60% 给历史，40% 给检索上下文）
- [ ] 实现 `truncate_history_by_tokens()`：按 Token 预算截断，保留最近完整轮次，不切割单条消息
- [ ] 实现 `estimate_context_tokens()`：在发送 LLM 请求前预估总 Token（history + system prompt + retrieved chunks + question）

#### 上下文压缩与摘要

- [ ] 实现滑动窗口摘要：当历史超出 Token 预算时，对最早的 N 轮对话调用轻量 LLM 生成摘要，替换原始文本
- [ ] 实现关键信息提取：从长历史中抽取实体、主题、已确认的事实，生成 "running memory" 注入系统提示
- [ ] 为 `QASession` 添加 `summary` 字段，缓存会话级摘要，避免重复计算

#### 可配置化与策略选择

- [ ] `core/config.py` 添加上下文管理配置：
  - `chat_history_strategy: Literal["truncate", "summarize", "sliding_window"]`
  - `chat_history_max_turns: int`
  - `chat_history_max_tokens: int`
  - `chat_summary_trigger_turns: int`
- [ ] `schemas.py` 添加 `ContextManagementSettings` 和更新接口
- [ ] Settings 前端页面添加上下文管理策略选择器

#### 边界与异常处理

- [ ] 处理并发请求时的历史顺序问题：同一 session 的两个并发请求，确保 transcript 追加顺序正确
- [ ] 处理流式中断：若 `streamAnswer` 中途断开，已生成的部分 answer 是否写入 transcript 需可配置
- [ ] 超长单轮消息防护：单条 user/assistant 消息超过 Token 阈值时拒绝或截断

#### 测试

- [ ] 编写 `test_context_manager.py`：测试 Token 截断、摘要替换、预算计算
- [ ] 编写 E2E 测试：验证 10 轮对话后仍能正确引用第 1 轮的内容（摘要质量测试）

---

## P1 🟠 重要（影响可维护性、扩展性和代码健康）

### 后端代码重构

- [ ] 拆分 `api.py`（546 行）为子 Router 模块：
  - [ ] `routes/courses.py` — 课程管理
  - [ ] `routes/ingestion.py` — 文件导入与批次
  - [ ] `routes/search.py` — 搜索与检索
  - [ ] `routes/agent.py` — Agent 问答与流式输出
  - [ ] `routes/sessions.py` — 会话管理
  - [ ] `routes/settings.py` — 模型设置
- [ ] 拆分 `ingestion.py`（886 行）：
  - [ ] `services/course_service.py` — 课程创建/解析/查询
  - [ ] `services/file_ingest_service.py` — 单文件导入流程
  - [ ] `services/batch_service.py` — 批量导入编排
- [ ] 提取公共 `source_type_from_path()` 到 `core/utils.py`，消除三处重复
- [ ] 合并 `choose_llm_graph_chunks()` 的重复定义（`ingestion.py` 和 `concept_graph.py`）
- [ ] 将 `ensure_schema()` 从模块级执行改为 FastAPI lifespan 事件中调用
- [ ] 为 EmbeddingProvider / ChatProvider / VectorStore 定义 Protocol/ABC 接口，通过依赖注入传入
- [ ] 统一 DB Session 管理：消除手动 `SessionLocal()`，全部使用 `Depends(get_db)` 或明确的后台任务 session 工厂
- [ ] 将 `get_settings()` 从服务类内部调用改为构造函数注入，降低测试耦合

### 前端代码重构

- [ ] 拆分 `qa-workspace.tsx`（676 行）：
  - [ ] `components/qa/chat-composer.tsx`
  - [ ] `components/qa/message-list.tsx`
  - [ ] `components/qa/sessions-drawer.tsx`
  - [ ] `components/qa/trace-drawer.tsx`
  - [ ] `components/qa/citations-drawer.tsx`
- [ ] 拆分 `search-workspace.tsx` 和 `upload-workspace.tsx` 中的子组件
- [ ] 添加全局 React Error Boundary
- [ ] 将硬编码中英混合字符串提取为常量文件

### DevOps

- [x] 编写 `apps/api/Dockerfile`
- [x] 编写 `apps/web/Dockerfile`（当前为 dev 模式）
- [ ] 更新 `apps/web/Dockerfile` 支持生产构建（`npm run build` + `next start`）
- [x] 更新 `infra/docker-compose.yml` 加入 API + Web 服务编排
- [ ] 添加 API 版本前缀 `/api/v1/`

### 知识图谱增量更新

> 当前 `rebuild_course_graph` 是 wipe+full rebuild，课程规模增大后不可持续。

- [ ] 将 `rebuild_course_graph` 从全量重建改为增量 merge：只处理新增/变更文档的 chunks
- [ ] 为概念/关系添加更新标记或版本号，支持部分更新而非全量替换
- [ ] 评估并发度：将 graph extraction 的 `semaphore=2` 改为可配置，大课程可适度提升

### 系统效果评估

#### 检索质量评估（自动化指标）

- [ ] 创建 `eval/` 目录结构和评估框架入口 `eval/run_full_eval.py`
- [ ] 标注检索评估集 `eval/retrieval_benchmark.json`（≥50 条 query + 相关 chunk_id）
- [ ] 实现检索评估脚本 `eval/eval_retrieval.py`：
  - [ ] MRR（Mean Reciprocal Rank）
  - [ ] Hit@3 / Hit@6
  - [ ] Precision@K
  - [ ] NDCG@K
- [ ] 对比实验：Dense only vs Lexical only vs Hybrid (WSF)，验证混合检索增益
- [ ] 对比实验：不同 `chunk_size`（800 / 1200 / 1600）对检索效果的影响
- [ ] 对比实验：不同 `alpha` 权重（0.3 / 0.7 / 0.85）对不同类型查询的影响

#### 知识图谱质量评估

- [ ] 标注 2-3 章的概念/关系 ground truth `eval/graph_ground_truth.json`
- [ ] 实现图谱评估脚本 `eval/eval_graph.py`：
  - [ ] 概念抽取 Precision / Recall / F1（名称模糊匹配）
  - [ ] 关系抽取 Precision / Recall（宽松匹配 + 严格匹配）
  - [ ] 图谱结构健康度（连通分量、孤立节点比例、平均度数）
- [ ] 对比实验：LLM 抽取 vs 纯启发式抽取，验证 LLM 调用是否值得

#### Agent QA 质量评估

- [ ] 标注 QA 评估集 `eval/qa_benchmark.json`（30-50 题，含参考答案、预期路由、来源文档）
  - [ ] 事实型问题（≥10 条）
  - [ ] 比较型问题（≥8 条）
  - [ ] 应用型问题（≥7 条）
  - [ ] 综合跨章节问题（≥5 条）
  - [ ] 边界测试（课程无关 / 模糊 / 过短问题，≥5 条）
- [ ] 实现自动评估脚本 `eval/eval_qa_auto.py`（层 1）：
  - [ ] 路由准确率（实际 route vs expected_route）
  - [ ] 引用命中率（citations 中的 doc_title vs expected_source_docs）
  - [ ] 答案非空率
  - [ ] ROUGE-L（与参考答案的文本重叠）
  - [ ] 延迟分布（从 trace 的 duration_ms 统计）
- [ ] 实现 LLM-as-Judge 评估脚本 `eval/eval_qa_llm_judge.py`（层 2）：
  - [ ] 忠实性（Faithfulness）1-5 分
  - [ ] 相关性（Relevance）1-5 分
  - [ ] 完整性（Completeness）1-5 分
  - [ ] 引用质量（Citation Quality）1-5 分
- [ ] 实现评估报告生成器，输出 Markdown 格式报告（按难度/类别分组统计）

### 数据库迁移

- [ ] 引入 Alembic 管理数据库迁移，替换手写 `SCHEMA_PATCHES`
- [ ] 为 SQLite fallback 数据库添加迁移兼容

---

## P2 🟡 改善（提升工程规范、性能和兼容性）

### 性能优化

- [ ] 为 BM25 索引添加按课程缓存（Redis 或内存 LRU），chunk 变更时增量更新而非全量重建
- [ ] 限制 lexical_search 的内存占用：分页加载 chunks 或建立倒排索引，避免大课程全表扫描
- [ ] 将 SQLAlchemy 切换到 async 引擎（`create_async_engine` + `AsyncSession`）
- [ ] Agent 图节点中的 DB 操作改为通过 service 层间接调用，解耦持久化

### 代码规范

- [ ] 添加 `ruff` 配置到 `apps/api/pyproject.toml`（lint + format）
- [ ] 添加 Prettier 配置到 `apps/web`
- [ ] 添加 pre-commit hooks（ruff、prettier、eslint、typecheck）
- [ ] 统一异常处理：将宽泛的 `except Exception` 替换为具体异常类型
- [ ] 将 Agent 路由中的硬编码关键词（"hello"、"exercise" 等）提取为可配置常量

### 前端增强

- [ ] 添加 Web Vitals / 性能监控
- [ ] 添加 i18n 框架（next-intl 或 react-i18next），统一中英文管理
- [ ] 为 `NetworkCanvas` 组件添加大图谱的虚拟化/懒加载
- [ ] `network-canvas.tsx` 解耦 ECharts 私有 API，使用公开配置实现力导向布局控制

### 基础设施兼容性

- [ ] 对齐 Qdrant 客户端与服务端版本（当前 client 1.17.1 vs server 1.13.2）
- [ ] 为 `.env` 挂载添加只读模式（`ro`），避免容器内写入导致文件竞争
- [ ] 评估 model-bridge 的可移植性：为 Linux/macOS 提供等效启动脚本（`start-app.sh`）

---

## P3 🔵 锦上添花（长期规划）

### 数据管理

- [ ] 添加数据备份/恢复脚本
- [ ] 实现跨课程的知识迁移（概念别名、通用公式库）

### CI/CD

- [ ] 添加 GitHub Actions 工作流：
  - [ ] Python lint + typecheck + test
  - [ ] TypeScript lint + typecheck + test
  - [ ] Docker 镜像构建
- [ ] 添加 PR 模板和 branch protection rules

### 文档

- [ ] 编写 `CONTRIBUTING.md`
- [ ] 添加 `CHANGELOG.md`
- [ ] 为核心 Service 层添加 docstring
- [ ] 生成 API 文档补充说明（FastAPI `/docs` 之外的使用指南）

### 架构演进

- [ ] 考虑将 Celery Worker 改为 FastAPI BackgroundTasks + asyncio（简化部署）
- [ ] 添加 rate limiting 中间件
- [ ] 添加 structured logging（JSON 格式日志）
- [ ] 考虑添加 OpenTelemetry 可观测性
- [ ] 为 SQLite fallback 数据库添加迁移兼容

---

## 进度跟踪

| 阶段 | 总任务数 | 已完成 | 进度 |
|------|---------|--------|------|
| P0 紧急（事务/并发/锁/跨存储） | 22 | 18 | 82% |
| P0 紧急（GraphRAG） | 13 | 7 | 54% |
| P0 紧急（测试 + 安全） | 15 | 14 | 93% |
| P0 紧急（外部服务/数据质量） | 8 | 6 | 75% |
| P0 紧急（工业级上下文管理） | 18 | 0 | 0% |
| P1 重要（代码重构 + DevOps） | 27 | 3 | 11% |
| P1 重要（效果评估） | 24 | 0 | 0% |
| P1 重要（图谱增量更新） | 3 | 0 | 0% |
| P2 改善 | 16 | 0 | 0% |
| P3 锦上添花 | 12 | 0 | 0% |
| **总计** | **159** | **48** | **30%** |
