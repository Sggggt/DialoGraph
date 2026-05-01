# DialoGraph 检索架构升级方案

> **背景**：当前系统 Dense Answerable@6 = 0.9333，而 Hybrid API Answerable@6 = 0.6167——融合后反而更差。本文档系统性地分析根因并给出工业级替代架构。

---

## 1. 诊断：现有缺陷的根因图

```
问题树
├── [P1] RRF 丢弃相似度强弱          → 强语义 chunk 优势被削弱
├── [P2] Dense / Lexical 等权          → 弱信号与强信号同等话语权
├── [P3] Lexical 不是真 BM25          → 偏向长 chunk / 高频术语
├── [P4] 候选池 top_k×4 太小          → 好答案在 top50 外进不了融合池
├── [P5] 无 Reranker                   → 只能"像不像"，无法判断"能不能回答"
├── [P6] 不按 query 类型自适应         → 定义题/公式题/比较题一套权重
├── [P7] Graph seed 依赖 Hybrid        → 坏 seed → 图噪声放大
└── [P8] Graph boost 无语义校验        → relation.confidence 不等于回答相关性
```

---

## 2. 学术/工业界现状速览

| 技术 | 代表工作 | 现状（2024-25） |
|------|---------|----------------|
| **BM25** | Robertson 1994 / rank-bm25 | 仍是稀疏基线首选；`rank-bm25` 已在 pyproject.toml 中，**未被使用** |
| **Dense bi-encoder** | DPR / BGE-M3 | 主流；cosine similarity 保留绝对分值 |
| **Learned sparse** | SPLADE-v3 | 比 BM25 强但需要单独模型；本项目暂不引入 |
| **Weighted Score Fusion** | Milvus / Weaviate 2024 | 替代 RRF；对分值归一化后加权求和，保留强弱信息 |
| **Cross-Encoder Rerank** | BGE-reranker-v2-m3 | 2024 标准配置；`sentence-transformers` 已在 optional deps |
| **ColBERT late-interaction** | PLAID / RAGatouille | 精度高但部署重；本项目建议留作 Phase 2 |
| **HyDE** | Gao et al. 2022 | 对短/模糊 query 有效；引入 1 次 LLM 调用 |
| **Query type routing** | 工业实践 | 轻量 heuristic 分类器即可区分定义/公式/比较题 |
| **Graph semantic gate** | GraphRAG / HippoRAG | 用 query-chunk cosine 过滤 graph expand 结果 |

---

## 3. 目标架构：三阶段检索流水线

```
                         ┌─────────────────────────────────────────────────┐
                         │              Query Intelligence Layer            │
 User Query ──────────▶  │  1. Query Type Classifier (heuristic + LLM)    │
                         │  2. Query Rewriter / HyDE (可选，对模糊 query)  │
                         │  3. 输出: query_type, weight_α, retrieval_k     │
                         └──────────┬──────────────────────────────────────┘
                                    │
              ┌─────────────────────▼──────────────────────┐
              │           Multi-Channel Recall              │
              │                                             │
              │  Channel A: Dense (Qdrant, top-K_dense)    │
              │  Channel B: BM25  (rank-bm25, top-K_bm25)  │
              │  Channel C: (可选) Hybrid DB native search  │
              │                                             │
              │  Fusion: Weighted Score Fusion              │
              │    normalized_dense × α  +                  │
              │    normalized_bm25  × (1-α)                 │
              │  α 由 Query Intelligence 输出决定           │
              └──────────────────┬──────────────────────────┘
                                 │ top-N candidates (N = 50-100)
              ┌──────────────────▼──────────────────────────┐
              │         Cross-Encoder Rerank Layer           │
              │                                             │
              │  Model: BAAI/bge-reranker-v2-m3             │
              │  Input: (query, chunk_content) pairs        │
              │  Output: relevance score ∈ [0, 1]           │
              │  → top-K final chunks                       │
              └──────────────────┬──────────────────────────┘
                                 │
              ┌──────────────────▼──────────────────────────┐
              │       Graph-Enhanced Augmentation (可选)     │
              │                                             │
              │  Semantic Gate: cosine(query_vec, chunk_vec)│
              │    > θ=0.55 才允许 graph expand chunk 入池  │
              │  Graph boost 改为加法混合，不覆盖 rerank 分  │
              └─────────────────────────────────────────────┘
                                 │ final top-K
                                 ▼
                           LLM Answer Generator
```

---

## 4. 各模块详细设计

### 4.1 Query Intelligence：自适应权重

#### 查询类型分类

| query_type | 判断规则（heuristic） | α (Dense权重) | recall_k |
|------------|----------------------|--------------|----------|
| `definition` | 含 "什么是/define/meaning/概念" | 0.85 | 60 |
| `formula` | 含数学符号、"公式/theorem/proof/complexity/O(" | 0.30 | 80 |
| `example` | 含 "例子/example/举例/instance" | 0.70 | 60 |
| `comparison` | 含 "比较/versus/区别/difference" | 0.75 | 80 |
| `procedure` | 含 "步骤/algorithm/how to/流程" | 0.75 | 60 |
| `default` | 其他 | 0.72 | 64 |

**为什么 formula 的 α=0.30（Lexical 主导）？**
因为 Dense 无法区分 "Dijkstra" 和 "Bellman-Ford"——公式/定理名是精确符号匹配，BM25 反而更可靠。

#### HyDE（可选，仅 definition / default 类型触发）

```python
# 仅在 query token < 8 且不含专有术语时触发
# 生成一个假设答案段落，用其向量而非 query 向量做 dense 召回
# 额外 LLM 调用 ~200ms，建议用 cache (query hash → hyde_vec)
```

---

### 4.2 真 BM25 替代当前 lexical_search_chunks

`rank-bm25` 已经在 pyproject.toml 中列为依赖但**从未被调用**。

#### 改造要点

```python
# 当前 (retrieval.py:287)
overlap = sum(haystack.count(term) for term in query_terms)

# 目标：BM25Okapi
from rank_bm25 import BM25Okapi

# 建立 per-course 内存 BM25 索引（启动时 + 摄入后失效）
# tokenizer: jieba (中文) + re.findall 英文 + 数学符号保留
```

#### BM25 索引管理策略

```
IndexRegistry (单例)
├── course_id → BM25Index
│   ├── corpus: list[str]          # chunk content
│   ├── chunk_ids: list[str]       # 对应 chunk_id
│   ├── bm25: BM25Okapi            # rank-bm25 对象
│   └── built_at: datetime
└── invalidate(course_id)          # 摄入完成后调用
```

内存开销估算：1万个 chunk × 平均 200 token = ~20MB，可接受。

---

### 4.3 Weighted Score Fusion 替代 RRF

#### 当前问题

```python
# retrieval.py:166-176 (当前 RRF)
scores["rrf_dense"]   = 1 / (60 + rank)  # rank1=0.0164, rank10=0.0143，差7%
scores["rrf_lexical"] = 1 / (60 + rank)  # 和 dense 等权
fused_score = rrf_dense + rrf_lexical
```

#### 新方案：Min-Max 归一化 + 自适应加权

```python
def weighted_score_fusion(
    dense_results: list[dict],   # 含原始 cosine score
    bm25_results: list[dict],    # 含 BM25 原始分
    alpha: float,                # 由 query_type 决定
) -> dict[str, float]:
    
    # 1. Min-Max 归一化到 [0,1]
    def normalize(scores: list[float]) -> list[float]:
        lo, hi = min(scores), max(scores)
        if hi == lo:
            return [1.0] * len(scores)
        return [(s - lo) / (hi - lo) for s in scores]
    
    dense_scores = normalize([r["score"] for r in dense_results])
    bm25_scores  = normalize([r["score"] for r in bm25_results])
    
    # 2. 构建 chunk_id → 归一化分 的映射
    fused: dict[str, float] = {}
    for item, norm_s in zip(dense_results, dense_scores):
        fused[item["chunk_id"]] = fused.get(item["chunk_id"], 0.0) + alpha * norm_s
    for item, norm_s in zip(bm25_results, bm25_scores):
        fused[item["chunk_id"]] = fused.get(item["chunk_id"], 0.0) + (1 - alpha) * norm_s
    
    return fused  # chunk_id → fused_score
```

**为什么归一化比 RRF 好？**
- Dense rank1 cosine=0.92，rank2 cosine=0.61 → 归一化后分差 ~0.5
- RRF rank1 vs rank2 分差 < 2%
- 真正高置信度的命中在 WSF 里会有显著优势

---

### 4.4 Cross-Encoder Reranker

这是最高 ROI 的改造。rerank 可以直接判断"这段文字能回答这个问题吗"。

#### 模型选择

| 模型 | 参数量 | 语言 | 推理速度 | 推荐场景 |
|------|--------|------|----------|---------|
| `BAAI/bge-reranker-v2-m3` | 568M | 多语言 | ~50ms/32对 | **首选**，中英文混合课程 |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | 22M | 英文 | ~5ms/32对 | 纯英文，低延迟 |
| `BAAI/bge-reranker-v2.5-gemma2-lightweight` | 2B | 多语言 | ~200ms/32对 | 高精度场景 |

#### 集成方案

```python
# app/services/reranker.py (新建)

from sentence_transformers import CrossEncoder
import threading

class RerankerProvider:
    _instance = None
    _lock = threading.Lock()
    
    @classmethod
    def get(cls) -> "RerankerProvider | None":
        """懒加载单例，首次调用时加载模型（~3-5s），之后复用"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    try:
                        cls._instance = cls()
                    except Exception:
                        return None  # graceful degradation
        return cls._instance
    
    def __init__(self):
        self.model = CrossEncoder(
            "BAAI/bge-reranker-v2-m3",
            max_length=512,
            device="cpu",          # 生产可改 cuda
        )
    
    def rerank(
        self, 
        query: str, 
        candidates: list[dict],
        top_k: int,
    ) -> list[dict]:
        if not candidates:
            return candidates
        
        pairs = [[query, item["content"][:512]] for item in candidates]
        scores = self.model.predict(pairs, show_progress_bar=False)
        
        for item, score in zip(candidates, scores):
            item["metadata"].setdefault("scores", {})["rerank"] = float(score)
        
        ranked = sorted(candidates, key=lambda x: x["metadata"]["scores"]["rerank"], reverse=True)
        return ranked[:top_k]
```

#### 性能考量

- 召回候选 N=64，rerank top-K=8：每次查询 64 次 CE forward pass
- CPU 推理：`bge-reranker-v2-m3` ~150-300ms for 64 pairs（可接受）
- 可用 ONNX 量化版本将延迟降至 30-60ms

---

### 4.5 Graph 语义校验 Gate

当前问题：`graph_boost = confidence × importance_score`，这不代表 chunk 能回答 query。

#### 改造：相似度阈值 Gate

```python
# 在 graph_enhanced_search 中，graph expand 后做语义过滤
# 利用 query 向量（dense_search 已有）与 expand chunk 向量做 cosine

async def graph_enhanced_search_v2(...):
    base_results = await hybrid_search_v2(...)
    
    # 获取 query 向量（复用 dense_search 已算的）
    query_vec = await embedder.embed_texts([query])[0]
    
    # graph expand...（同现有逻辑）
    
    # ✅ 新增：语义 gate
    SEMANTIC_GATE_THRESHOLD = 0.55
    for chunk in expand_chunks:
        chunk_vec = vector_store.get_points([chunk.id])[0]["vector"]
        sim = cosine_similarity(query_vec, chunk_vec)
        if sim < SEMANTIC_GATE_THRESHOLD:
            continue  # 不让图噪声进入候选池
        graph_boost = boost_by_chunk.get(chunk.id, 0.0) * sim  # 加权
        ...
```

#### Agent 多跳：更换 seed 策略

```
现在：graph_enhanced_search(hybrid_seed)  ← seed 错误则全错

建议：
1. 先用 dense-only top-20 作为 graph seed（dense 质量更高）
2. graph expand 后经过语义 gate
3. 最后对 base + graph 合并候选整体做 rerank
```

---

## 5. 完整新流程对比

### 当前流程

```
query
  → dense_search(top_k×4=32) + lexical_term_count(top_k×4=32)
  → RRF(k=60, α=0.5)
  → top_k=8
  → LLM
```

### 新流程

```
query
  → QueryTypeClassifier → (query_type, α, recall_k)
  → [可选] HyDE 扩展 query 向量
  ↓
  并行:
    dense_search(recall_k=64, Qdrant cosine)
    bm25_search(recall_k=64, rank-bm25 in-memory)
  ↓
  WeightedScoreFusion(α by query_type, min-max normalize)
  → union top-N=64 candidates
  ↓
  CrossEncoderRerank(bge-reranker-v2-m3)
  → top_k=8 final chunks
  ↓
  [multi_hop only] GraphSemanticGate(cosine > 0.55) + expand
  → rerank again on merged pool
  ↓
  LLM Answer Generator
```

---

## 6. 改造优先级与落地路径

### Phase 0（零风险，立即可做）

| 优先 | 改动 | 文件 | 预期收益 |
|------|------|------|---------|
| ⭐⭐⭐ | 把 `lexical_search_chunks` 替换为真 BM25 | `retrieval.py` | 修复 P3，对公式/术语 query 帮助巨大 |
| ⭐⭐⭐ | 把 `dense_search` 召回量从 `top_k×4` 改为固定 64 | `retrieval.py` | 修复 P4 |
| ⭐⭐ | RRF → Weighted Score Fusion，α=0.72 固定值先 | `retrieval.py` | 修复 P1+P2 |

### Phase 1（主要收益，约 1-2 天）

| 优先 | 改动 | 文件 | 预期收益 |
|------|------|------|---------|
| ⭐⭐⭐ | 集成 CrossEncoder Reranker（懒加载单例） | 新增 `reranker.py` | 修复 P5，这是最大的质量跳跃 |
| ⭐⭐⭐ | Query Type Classifier + 自适应 α | `retrieval.py` + 新增 `query_classifier.py` | 修复 P6 |

### Phase 2（架构升级）

| 优先 | 改动 | 文件 | 预期收益 |
|------|------|------|---------|
| ⭐⭐ | Graph 语义 Gate (cosine threshold) | `retrieval.py` | 修复 P7+P8，图增强真正有效 |
| ⭐⭐ | Agent 多跳改用 dense-only seed | `agent_graph.py` | 修复 P7 |
| ⭐ | HyDE（仅 short/ambiguous query） | `retrieval.py` | 修复长尾 query |

---

## 7. 关键参数速查

```python
# 建议初始值（可通过评估集调整）
RECALL_K_DEFAULT        = 64    # 替代 top_k * 4
RECALL_K_FORMULA        = 80    # 公式题多召回
RERANK_TOP_K            = top_k # 通常 8
ALPHA_DEFAULT           = 0.72  # Dense 权重
ALPHA_FORMULA           = 0.30  # 公式题 Lexical 主导
ALPHA_DEFINITION        = 0.85  # 定义题 Dense 主导
GRAPH_SEMANTIC_GATE     = 0.55  # cosine 阈值
HYDE_TOKEN_THRESHOLD    = 8     # query token 数 < 8 才触发 HyDE
RERANKER_MODEL          = "BAAI/bge-reranker-v2-m3"
BM25_K1                 = 1.5   # BM25Okapi 参数
BM25_B                  = 0.75  # BM25Okapi 参数
```

---

## 8. 预期效果估算

| 指标 | 当前 | Phase0 后 | Phase1 后 |
|------|------|----------|----------|
| Hybrid Answerable@6 | 0.617 | ~0.72 | ~0.87 |
| Graph-enhanced 效果 | 很差 | 持平 | 有效（Phase2后） |
| P99 延迟 | ~800ms | ~850ms | ~1100ms（含rerank）|
| 内存增量 | 0 | ~20MB(BM25) | ~2GB(reranker模型) |

> reranker 模型 2GB 是一次性加载，之后推理每次 ~150-300ms。对于课程知识库场景完全可接受。

---

## 9. 与现有代码的兼容说明

- **Qdrant 不需要改**：dense 召回扩大 limit 即可，`vector_store.search` 接口不变
- **`score_chunk_bonus` 可保留**：作为 BM25 归一化前的预处理加成，或在 rerank 后作为 tiebreaker
- **Agent graph 节点不需改**：`HybridRetriever` 调用 `hybrid_search_chunks`，只需让新函数签名兼容
- **`sentence-transformers` 已在 optional deps**：`rerank = ["sentence-transformers>=3.3.1"]`，只需 `uv pip install -e ".[rerank]"`
- **`rank-bm25` 已在 main deps**：直接使用，零新依赖
