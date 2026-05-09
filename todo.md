# DialoGraph TODO

> 当前清单面向现有架构维护与下一步扩展，不记录历史版本信息。默认执行口径：全栈 Docker、容器内系统 Python、真实 `.env` API、真实本地课程数据、`ENABLE_MODEL_FALLBACK=false`、`ENABLE_DATABASE_FALLBACK=false`。
>
> 本清单已覆盖 2026-05-09 全面技术评价中发现的问题，完整评价报告见 `output/project_evaluation_report.md`。

## 当前状态

- [x] 父子块入库与向量入库：active chunk 使用 parent/child metadata，child 记录 `parent_chunk_id`。
- [x] 上下文增强向量：child embedding input 包含 parent summary、相邻 child summary、关键词、表格和公式标记。
- [x] Small-to-Big 检索：Dense/Qdrant 与 BM25 默认召回 child，返回结果装配 parent context。
- [x] 图谱增强检索：多跳类问题通过概念关系扩展 evidence chunk，并复用 parent context 装配。
- [x] Cross-Encoder 精排：`RERANKER_ENABLED` 控制真实 reranker，关闭时使用本地轻量排序信号。
- [x] 无 fallback 运行路径：Docker compose 固定关闭 model/database fallback，运行时检查暴露阻断项。
- [x] 数据质量脚本：保留容器内 `quality_gate.py`、`analyze_chunk_quality.py`、`reembed_all_chunks.py`、`reembed_with_enhancement.py`、`reingest_all_courses.py`。
- [x] 文档：中英文 README 同步描述技术栈、核心算法、架构图、配置和验收命令。
- [x] 启动脚本拆分：`start-app.bat` 不再强制重建镜像，新增 `rebuild-images.bat` 用于单独重建容器镜像。

## P0：阻塞生产 / 大规模扩展

### P0-2 数据一致性

- [ ] 全量重解析单门课程后清理 inactive 数据和旧向量：
  ```powershell
  docker exec course-kg-api python /app/scripts/reingest_all_courses.py --course-name "课程名称" --cleanup-stale
  ```
- [ ] 确认 active DB chunks 与 Qdrant points 对齐，零向量为 0，孤儿向量为 0。
- [ ] 确认 active child 均有 `parent_chunk_id`，检索结果 metadata 包含 `retrieval_granularity=child_with_parent_context`。
- [ ] 确认 `.env` 与 `.env.example` 参数名一致，不暴露密钥值。

### P0-3 性能瓶颈（扩展前必须解决）

- [x] **Web 容器运行 dev 模式**：已改为 `npm run build` + `next start` 生产模式。
- [ ] **Lexical 检索全表扫描**：当前每次查询拉取全量 active chunks 内存构建 BM25，时间/空间复杂度 O(N)，chunk 数过万时不可扩展。
  - 方案 A：PostgreSQL `tsvector` 全文索引 + `to_tsvector`/`ts_rank_cd` 替代内存 BM25。
  - 方案 B：引入 Meilisearch 侧车容器，专门承担 lexical 检索，API 层做 adapter。
  - 验收：单课程 5 万 child chunks 下 lexical recall 延迟 < 200ms。
- [x] **图谱非增量全量重建**：`rebuild_course_graph` 先 DELETE 全课概念/关系/别名再重建，重建期间图谱为空，失败则数据丢失。
  - 方案：影子图谱（shadow graph）策略——在独立表空间或临时 schema 构建新图谱，验证通过后原子切换，旧图谱保留为备份。
  - 验收：重建期间 `/graph` 和 `/concepts` 查询不返回空数据，重建失败可自动回滚到旧图谱。
- [ ] **Web 容器运行 dev 模式**：`apps/web/Dockerfile` 使用 `npm run dev`，暴露热重载 WebSocket 和源码映射，不适合生产。
  - 方案：改为 `next build` + `next start` 生产模式，或提供 `Dockerfile.prod` 多阶段构建。
  - 验收：`docker compose up web` 后容器内进程为 `next start`，端口 3000 响应正常。

- [ ] **Agentic RAG 长链路导致超时风险**：完整的问答包含感知、规划、生成和可能的 EvidenceEvaluator 重试，容易触发 FastAPI 或前端 HTTP 超时。\n  - 方案：在 Perception 阶段尽早通过 SSE 下发 Agent 状态保持 TCP 连接；在 FastAPI 侧添加 Semaphore 严格限制 Agent 并发数，避免打满模型 API 的并发槽位。\n
## P1：显著影响体验 / 效率

### P1-1 检索与问答质量

- [ ] 记录每类 query 的 top-k 证据质量、parent context 覆盖率、reranker_called 率和延迟。
- [ ] 为 QA 增加引用质量检查：答案必须能映射到 citation 和 parent context。
- [ ] 评估图谱扩展的噪声：对低置信关系设置查询类型或分数阈值，graph boost 后增加二次精排。
- [x] **Agent Document Grader 改进**：已引入 query-document embedding similarity 融合评分，`grade_score = 0.4 * overlap + 0.6 * cosine_sim`。

- [ ] **DocumentGrader 重叠度计算的脆弱性**：计算 $r_{overlap}$ 时，如果是中英混合查询，纯词面交集容易因分词不一致而过低。\n  - 方案：对 $T_q$ 和 $T_d$ 使用对齐的归一化处理（转小写、去标点）和一致的健壮分词器（如 jieba 或统一按 ngram 处理）。\n- [ ] **跨语言翻译导致延迟增加**：RetrievalPlanner 每次均调用 LLM 进行翻译，对简单查询过重。\n  - 方案：在 Redis 中维护一个高频双语术语表（结合概念别名），在 Perception 阶段命中图谱别名时可直接跳过在线翻译，降低延迟。\n
### P1-2 性能与吞吐

- [~] **Redis 缓存利用**：配置项 `retrieval_layer_enabled`、`RETRIEVAL_CACHE_TTL_SECONDS` 已添加；embedding / 检索结果缓存接口已设计，待完整接入 HybridRetriever 与 embedding pipeline。
  - 方案：
    - 缓存热门 query 的 embedding 向量（TTL 绑定 embedding_text_version）。
    - 缓存检索结果（hybrid search top-k，TTL 绑定课程最新 document_version 时间戳）。
    - 缓存运行时设置快照和健康检查结果（短 TTL，如 30s）。
  - 验收：相同 query 二次查询延迟降低 50% 以上，cache miss 时行为正确。
- [ ] **Embedding batch size 扩容**：当前硬上限 10，大规模导入时 API 调用次数多、网络 RTT 占比高。
  - 方案：将 `EMBEDDING_BATCH_SIZE` 默认值提升至 50~100；在 `embeddings.py` 中增加本地 embedding 模型 fallback 路径（如 `bge-m3` 离线嵌入）。
  - 验收：导入 1000 个 chunks 的 embedding 阶段耗时降低 40% 以上。
- [ ] **Qdrant 异步写入**：当前 `upsert(wait=True)` 同步阻塞，高并发导入吞吐受限。
  - 方案：改为 `wait=False` 批量异步 upsert，后台任务抽样校验写入结果。
- [ ] **图谱抽取并发提升**：当前 `GRAPH_EXTRACTION_CONCURRENCY = 2`，大规模课程重建慢。
  - 方案：提升至 4~8，并增加每课程全局 semaphore 防止单课程独占资源；引入本地小模型做概念预抽取，减少 LLM 调用量。

### P1-3 工程维护

- [ ] **将 `api.py` 拆分为子路由**：courses、files、ingestion、search、qa、settings、maintenance 独立模块。
- [ ] **将 ingestion 服务拆分**：`ingestion.py` (1680 行) 拆分为 `parsing.py`、`chunking_service.py`、`embedding_service.py`、`batch_orchestrator.py`、`graph_builder.py`。
- [ ] **引入 Alembic 管理数据库 schema**：替代 `db.py` 中 `SCHEMA_PATCHES` 运行时补丁，提供版本化迁移、回滚能力。
  - 验收：新增/修改列通过 `alembic revision --autogenerate` 生成，旧数据兼容迁移。
- [ ] **为 `EmbeddingProvider`、`ChatProvider`、`VectorStore` 定义协议接口**（`typing.Protocol` 或抽象基类），降低测试耦合，支持 mock 注入。

## 已完成的 Agentic RAG 升级

- [x] **Schema 迁移**：`concepts` 和 `concept_relations` 新增 `source_document_version_ids`（JSON），支持增量图谱更新的来源追踪。
- [x] **增量图谱更新**：`incremental_update_course_graph()` 实现，仅对变更文档关联的图谱局部重算，保留未变更概念和关系。
- [x] **API / 前端增量重建**：`/maintenance/rebuild-graph` 支持 `mode=incremental|full`；前端 `GraphPanel` 支持选择重建模式。
- [x] **分层检索 Layer 2→3**：`HybridRetriever` 根据 query type 路由：
  - Fast (Layer 1)：Redis cache + dense recall（待完整接入）
  - Standard (Layer 2)：dense + BM25 hybrid
  - Deep Graph (Layer 3)：graph_enhanced_search_v2（centrality boost、community aggregation、Dijkstra path expansion）
- [x] **Graph Enhanced v2**：新增 centrality-based boost、community aggregation、2-3 hop Dijkstra path expansion、relation-type filtering。
- [x] **Agentic 节点**：新增 `RetrievalDecision`、`Reflection`、`CitationVerifier`、`AnswerCorrector`；增强 `DocumentGrader`（embedding similarity 融合）和 `ContextSynthesizer`（token budget 分配）。
- [x] **ChatProvider 扩展**：新增 `decide_retrieval`、`reflect_answer`、`verify_citations`。
- [x] **配置扩展**：新增 `retrieval_layer_enabled`、`enable_agentic_reflection`、`citation_verification_sample_max`、`reflection_max_retries`。
- [x] **文档同步**：中英文 README 更新架构图、分层检索、Agentic 闭环、配置表和验收清单；`apps/web/Dockerfile` 改为生产模式。

## 待收尾

- [ ] **Agentic 节点完整接线**：`build_agent_graph()` 条件边 wiring，`Reflection` / `AnswerCorrector` 接入 retry 循环。
- [ ] **Redis 缓存完整接入**：`HybridRetriever` 中 Fast layer 缓存命中/失效，`embedding` pipeline 缓存。

## P2：优化与增强

### P2-1 算法与策略优化

- [ ] **Context 截断策略精细化**：当前每个 chunk 固定截断 1800 字符，未根据 token 预算动态分配。
  - 方案：引入 tiktoken 计数，按 rerank score 动态分配每个 chunk 的上下文长度（高分 chunk 给更多 token，低分 chunk 给更少或丢弃）。
- [ ] **语义切分成本优化**：当前每个长 section 需额外一次 embedding API 调用做边界判断。
  - 方案：使用本地轻量模型（如 `all-MiniLM-L6-v2`）预计算句子/段落相似度，仅对模糊边界调用主 embedding 模型。
- [ ] **Dijkstra 推断路径长度扩展**：当前仅考虑 3-4 跳路径，可能遗漏有价值的长路径。
  - 方案：引入参数化跳数上限（如 `DIJKSTRA_MAX_HOPS`），并增加路径语义聚合分数（路径上所有边权加权平均）。
- [ ] **谱聚类依赖健壮性**：`scipy.sparse.linalg.eigsh` 为可选依赖，缺失时退化为简单二分。
  - 方案：将 scipy 设为必需依赖；或提供纯 NumPy 实现的降级谱聚类（幂迭代近似前 k 特征向量）。

### P2-2 容器与部署

- [ ] **Dockerfile 多阶段构建优化**：
  - API：`python:3.13-slim` builder 阶段安装编译依赖 → runtime 阶段仅复制 wheel，减小镜像体积。
  - Web：builder 阶段 `npm ci` + `next build` → runtime 阶段仅保留 `node_modules` 生产依赖 + `.next/standalone`，镜像可降至 < 200MB。
- [ ] **容器 rootless 运行**：API 和 Web 容器添加非 root `USER`，降低权限面。
- [ ] **引入 CI/CD 流水线**（如 GitHub Actions）：
  - PR 触发：后端 `pytest`、前端 `typecheck` + `lint` + `vitest`、Docker `build` smoke。
  - 主分支触发：镜像构建并推送至 registry（如 GitHub Packages）。
- [ ] **日志聚合**：uvicorn access log、错误堆栈配置 JSON 结构化输出，可选接入 Loki / ELK / Grafana。

### P2-3 前端与产品

- [ ] **搜索结果展示增强**：展示 child evidence、parent context、rerank score、dense/BM25/fused 分数明细。
- [ ] **QA trace 展示模型审计**：provider、external_called、fallback_reason、degraded_mode、latency。
- [ ] **Settings 页面 `.env` 热加载**：保持修改后不重启被覆盖，已部分实现，需增加防并发写入锁。
- [ ] **增加结构化日志和 trace id**：ingestion/retrieval/QA 全链路携带 `trace_id`，便于定位批处理和检索异常。
- [ ] **移动端适配**：当前布局为纯桌面端优化，引入 Tailwind 响应式前缀（`md:`、`lg:`），优先支持平板尺寸（768px+）。
- [ ] **图谱交互增强**：最短路径查询、子图导出（JSON/GEXF）、概念对比（高亮共同邻居）。
- [ ] **问答历史管理**：会话重命名、搜索、标签分类、批量删除。
- [ ] **批量操作**：文件上传后支持批量删除、批量重解析、批量移动课程。

## 脚本状态

| 脚本 | 状态 | 用途 |
| --- | --- | --- |
| `scripts/quality_gate.py` | 保留 | DB/Qdrant active 数量、零向量、孤儿向量、父子块健康检查 |
| `scripts/analyze_chunk_quality.py` | 保留 | 统计 chunk 类型、长度、表格/公式、embedding version、重复内容 |
| `scripts/reembed_all_chunks.py` | 保留 | 只修复零向量 chunk，使用当前 contextual embedding input |
| `scripts/reembed_with_enhancement.py` | 保留 | 按当前 embedding input 重嵌入 active chunks |
| `scripts/reingest_all_courses.py` | 保留 | 通过正式 ingestion pipeline 重解析一门或多门课程 |
| `scripts/repair_parent_child_links.py` | 保留 | 修复历史 child chunks 缺失的 `parent_chunk_id` |
| `scripts/docker_smoke.py` | 保留 | 真实 Docker API parse-search-QA smoke |
| `scripts/evaluate_existing_quality.py` | 保留 | 使用 qwen3.6-plus 对现有课程 search/QA 做轻量评估 |

