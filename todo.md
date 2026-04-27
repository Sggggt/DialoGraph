# DialoGraph 工程改进 TODO

> 基于工程水平分析报告和 ACID 事务审计报告，按优先级排列的改进清单。
> 当前总评：**B+ / A-（78/100）**，目标：**A（90+/100）**

---

## P0 🔴 紧急（阻碍项目可交付性）

### 事务安全与跨存储协调

> 基于 ACID 审计报告 v2，解决 DB / 向量存储 / 文件系统 三方数据一致性问题。

#### 原子性修复

- [ ] `ingest_file`: 将 `create_or_update_document` 内部的 `db.commit()`（L494）改为 `db.flush()`，推迟到函数末尾统一 commit
- [ ] `ingest_file`: 在 `vector_store.delete(stale_chunk_ids)` 前记录旧向量 ID 到补偿日志，失败时可恢复
- [ ] `ingest_file`: 将向量 `upsert` 移到 DB commit **之后**执行，失败时仅需重试向量写入而非回滚整个事务
- [ ] `upload_file` 路由: DB 写入失败时清理已落盘的孤儿文件（`stored_path.unlink(missing_ok=True)`）
- [ ] `remove_course_file`: 将文件删除（`unlink`）移到 `db.commit()` 之后，避免 DB 回滚但文件已删

#### 并发控制

- [ ] 为 `ingest_file` 添加基于 `source_path` 的应用级锁（`asyncio.Lock` 字典），防止同一文件并发解析
- [ ] 为 `register_uploaded_file` 添加 `SELECT ... FOR UPDATE`（PG）或等效排他查询，防止并发上传同名文件竞态
- [ ] `run_batch_ingestion` / `run_uploaded_files_ingestion`: 添加批次级互斥，防止同课程并发触发多个批次
- [ ] 评估 `asyncio.run()` 在 `BackgroundTasks` 中的使用，考虑改为 `asyncio.create_task()` 以复用事件循环

#### 锁机制增强

- [ ] `FallbackVectorStore`: 在 Windows 上用 `msvcrt.locking` 或 `portalocker` 替代纯线程锁，支持多进程安全
- [ ] `FallbackVectorStore._write`: Windows 上 `Path.replace()` 非原子，添加 `os.fsync()` 确保数据落盘
- [ ] 添加锁清理机制：为 `_VECTOR_FILE_LOCKS` 添加 LRU 淘汰或 WeakValueDictionary，避免长期运行泄漏

#### 跨存储一致性

- [ ] 设计并实现 `IngestionCompensationLog` 表，记录向量操作（delete/upsert），崩溃恢复时重放或回滚
- [ ] `ingest_file` 失败路径: 添加向量存储补偿清理（删除已写入的新向量、恢复已删除的旧向量）
- [ ] 在 `finalize_interrupted_batches()` 中增加向量存储一致性检查：对比 DB 中活跃 chunk ID 与向量存储中的 ID
- [ ] `rebuild_course_graph` 前添加向量存储健康检查，确认向量数据可用后再删除旧图

#### Agent 事务一致性

- [ ] `run_agent`: 在 `append_session_turn` 前用 `try/finally` 确保 transcript 一定被写入，即使后续操作失败
- [ ] 文档化 Agent 多次 commit 是有意的可观测性设计决策（在代码注释和 README 中说明）

### 测试基础设施

- [ ] 搭建 Python 测试框架（pytest + pytest-asyncio + httpx TestClient）
- [ ] 为 `parsers.py` 编写单元测试（覆盖 PDF / Markdown / Notebook / PPTX / DOCX / HTML 解析）
- [ ] 为 `chunking.py` 编写单元测试（切块大小、重叠、content_kind 推断）
- [ ] 为 `retrieval.py` 编写单元测试（lexical_search、hybrid_search、RRF 融合逻辑）
- [ ] 为 `ingestion.py` 核心流程编写集成测试（ingest_file 端到端）
- [ ] 为 `agent_graph.py` 编写测试（路由判断、查询重写、文档评分逻辑）
- [ ] 为 `concept_graph.py` 编写测试（概念提取、名称规范化、图谱合并）
- [ ] 为 `embeddings.py` 编写测试（fallback 逻辑、fake embedding 生成）
- [ ] 搭建前端测试框架（Vitest + Testing Library）
- [ ] 为 `lib/api.ts` 编写测试（API 客户端、SSE 流解析）
- [ ] 添加 `conftest.py`，提供测试用的 SQLite 内存数据库 fixture

### 安全加固

- [ ] 添加 API 认证中间件（API Key 或 JWT Bearer Token）
- [ ] 收紧 CORS 配置（替换 `allow_origins=["*"]` 为白名单）
- [ ] 为 `ensure_schema()` 中的 DDL 操作消除字符串拼接，改用参数化或 SQLAlchemy DDL API
- [ ] 对 `top_k` 等用户输入参数添加上限约束（如 `Field(le=50)`）

---

## P1 🟠 重要（影响可维护性和代码健康）

### 后端代码重构

- [ ] 拆分 `api.py`（375 行）为子 Router 模块：
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

- [ ] 编写 `apps/api/Dockerfile`
- [ ] 编写 `apps/web/Dockerfile`
- [ ] 更新 `infra/docker-compose.yml` 加入 API + Web 服务编排
- [ ] 添加 API 版本前缀 `/api/v1/`

### 系统效果评估

#### 检索质量评估（自动化指标）

- [ ] 创建 `eval/` 目录结构和评估框架入口 `eval/run_full_eval.py`
- [ ] 标注检索评估集 `eval/retrieval_benchmark.json`（≥50 条 query + 相关 chunk_id）
- [ ] 实现检索评估脚本 `eval/eval_retrieval.py`：
  - [ ] MRR（Mean Reciprocal Rank）
  - [ ] Hit@3 / Hit@6
  - [ ] Precision@K
  - [ ] NDCG@K
- [ ] 对比实验：Dense only vs Lexical only vs Hybrid (RRF)，验证混合检索增益
- [ ] 对比实验：不同 `chunk_size`（800 / 1200 / 1600）对检索效果的影响
- [ ] 对比实验：不同 RRF `k` 值（30 / 60 / 100）对融合排序的影响

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

---

## P2 🟡 改善（提升工程规范和性能）

### 性能优化

- [ ] 将 SQLAlchemy 切换到 async 引擎（`create_async_engine` + `AsyncSession`）
- [ ] Agent 图节点中的 DB 操作改为通过 service 层间接调用，解耦持久化
- [ ] `lexical_search_chunks` 全表扫描优化：添加数据库全文索引或使用 `rank_bm25`

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

---

## P3 🔵 锦上添花（长期规划）

### 数据库与数据管理

- [ ] 引入 Alembic 管理数据库迁移，替换手写 `SCHEMA_PATCHES`
- [ ] 为 SQLite fallback 数据库添加迁移兼容
- [ ] 添加数据备份/恢复脚本

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

- [ ] 为 Service 层添加 Protocol/ABC 抽象接口
- [ ] 考虑将 Celery Worker 改为 FastAPI BackgroundTasks + asyncio（简化部署）
- [ ] 添加 rate limiting 中间件
- [ ] 添加 structured logging（JSON 格式日志）
- [ ] 考虑添加 OpenTelemetry 可观测性

---

## 进度跟踪

| 阶段 | 总任务数 | 已完成 | 进度 |
|------|---------|--------|------|
| P0 紧急（事务/并发/锁/跨存储） | 19 | 0 | 0% |
| P0 紧急（测试 + 安全） | 15 | 0 | 0% |
| P1 重要（代码重构 + DevOps） | 20 | 0 | 0% |
| P1 重要（效果评估） | 24 | 0 | 0% |
| P2 改善 | 12 | 0 | 0% |
| P3 锦上添花 | 14 | 0 | 0% |
| **总计** | **104** | **0** | **0%** |
