# DialoGraph TODO

> 当前清单面向现有架构维护，不记录历史版本信息。默认执行口径：全栈 Docker、容器内系统 Python、真实 `.env` API、真实本地课程数据、`ENABLE_MODEL_FALLBACK=false`、`ENABLE_DATABASE_FALLBACK=false`。

## 当前状态

- [x] 父子块入库与向量入库：active chunk 使用 parent/child metadata，child 记录 `parent_chunk_id`。
- [x] 上下文增强向量：child embedding input 包含 parent summary、相邻 child summary、关键词、表格和公式标记。
- [x] Small-to-Big 检索：Dense/Qdrant 与 BM25 默认召回 child，返回结果装配 parent context。
- [x] 图谱增强检索：多跳类问题通过概念关系扩展 evidence chunk，并复用 parent context 装配。
- [x] Cross-Encoder 精排：`RERANKER_ENABLED` 控制真实 reranker，关闭时使用本地轻量排序信号。
- [x] 无 fallback 运行路径：Docker compose 固定关闭 model/database fallback，运行时检查暴露阻断项。
- [x] 数据质量脚本：保留容器内 `quality_gate.py`、`analyze_chunk_quality.py`、`reembed_all_chunks.py`、`reembed_with_enhancement.py`、`reingest_all_courses.py`。
- [x] 文档：中英文 README 同步描述技术栈、核心算法、架构图、配置和验收命令。
- [x] 对比实验目录：`comparative_experiment/` 整体进入 Git ignore，不参与当前源码跟踪。

## P0：交付前质量门禁

- [ ] 在 Docker 栈内运行默认单测：
  ```powershell
  docker exec course-kg-api python -m pytest
  ```
- [ ] 在 Docker 栈内运行检索和图谱关键测试：
  ```powershell
  docker exec course-kg-api python -m pytest tests/test_retrieval.py tests/test_p0_graph_security.py tests/test_enhanced_chunking.py
  ```
- [ ] 对当前课程运行 DB/Qdrant 健康门禁：
  ```powershell
  docker exec course-kg-api python /app/scripts/quality_gate.py --course-name "课程名称"
  ```
- [ ] 运行轻量端到端 smoke：
  ```powershell
  python scripts/docker_smoke.py --base-url http://127.0.0.1:8000/api
  ```
- [ ] 如需真实 E2E，显式开启：
  ```powershell
  docker exec -e RUN_NO_FALLBACK_E2E=1 course-kg-api python -m pytest -m no_fallback_e2e
  ```

## P0：数据一致性

- [ ] 全量重解析单门课程后清理 inactive 数据和旧向量：
  ```powershell
  docker exec course-kg-api python /app/scripts/reingest_all_courses.py --course-name "课程名称" --cleanup-stale
  ```
- [ ] 确认 active DB chunks 与 Qdrant points 对齐，零向量为 0，孤儿向量为 0。
- [ ] 确认 active child 均有 `parent_chunk_id`，检索结果 metadata 包含 `retrieval_granularity=child_with_parent_context`。
- [ ] 确认 `.env` 与 `.env.example` 参数名一致，不暴露密钥值。

## P1：检索与问答质量

- [ ] 为常见课程问题维护一组小型人工验收集，覆盖 definition、formula、example、comparison、multi-hop。
- [ ] 记录每类 query 的 top-k 证据质量、parent context 覆盖率、reranker_called 率和延迟。
- [ ] 为 QA 增加引用质量检查：答案必须能映射到 citation 和 parent context。
- [ ] 评估图谱扩展的噪声：对低置信关系设置查询类型或分数阈值。

## P1：工程维护

- [ ] 将 `api.py` 拆分为 courses、files、ingestion、search、qa、settings、maintenance 子路由。
- [ ] 将 ingestion 服务拆分为 course、file ingest、batch ingest、graph build、vector write 模块。
- [ ] 引入 Alembic 管理数据库 schema，替代运行时手写 schema patch。
- [ ] 为 `EmbeddingProvider`、`ChatProvider`、`VectorStore` 定义协议接口，降低测试耦合。
- [ ] 为 scripts 增加最小命令 smoke 测试，至少覆盖参数解析和 no-fallback 拒绝逻辑。

## P2：前端与可观测性

- [ ] 搜索结果展示 child evidence、parent context、rerank score、dense/BM25/fused 分数。
- [ ] QA trace 展示模型审计字段：provider、external_called、fallback_reason、degraded_mode。
- [ ] Settings 页面继续保持 `.env` 热加载，不重启后被覆盖。
- [ ] 增加结构化日志和 ingestion/retrieval trace id，便于定位批处理和检索异常。

## 脚本状态

| 脚本 | 状态 | 用途 |
| --- | --- | --- |
| `scripts/quality_gate.py` | 保留 | DB/Qdrant active 数量、零向量、孤儿向量、父子块健康检查 |
| `scripts/analyze_chunk_quality.py` | 保留 | 统计 chunk 类型、长度、表格/公式、embedding version、重复内容 |
| `scripts/reembed_all_chunks.py` | 保留 | 只修复零向量 chunk，使用当前 contextual embedding input |
| `scripts/reembed_with_enhancement.py` | 保留 | 按当前 embedding input 重嵌入 active chunks |
| `scripts/reingest_all_courses.py` | 保留 | 通过正式 ingestion pipeline 重解析一门或多门课程 |
| `scripts/docker_smoke.py` | 保留 | 真实 Docker API parse-search-QA smoke |
| `scripts/evaluate_existing_quality.py` | 保留 | 使用 qwen3.6-plus 对现有课程 search/QA 做轻量评估 |

## 测试状态

- 默认 `pytest` 排除 `fallback_compat` 与真实 E2E 标记。
- `test_retrieval.py` 覆盖 weighted fusion、rerank/no-rerank、零向量告警、child-to-parent context。
- `test_p0_graph_security.py` 覆盖 graph-enhanced search 与 parent context 装配。
- `test_enhanced_chunking.py` 覆盖父子切块、contextual embedding、表格/公式 metadata。
- `test_runtime_settings.py` 覆盖 `.env`/`.env.example` 参数名一致性和运行时检查。
