**English** | [中文](./README.md)

<p align="center">
  <img src="./assets/diagraph-logo.svg" alt="DialoGraph logo" width="132" height="132">
</p>

<h1 align="center">DialoGraph</h1>

DialoGraph is a Dockerized knowledge infrastructure system for local course materials. It parses PDFs, slides, documents, web pages, notebooks, images, and Markdown into searchable text chunks, Qdrant vectors, PostgreSQL sparse knowledge graphs, and citation-backed answers.

The default runtime uses real PostgreSQL, Qdrant, Redis, and an OpenAI-compatible model API. Model fallback and database fallback are disabled by default; production-quality validation does not use zero vectors, fake embeddings, local JSON retrieval, or extractive substitute answers.

## At A Glance

| Area                   | Implementation                                                                                                                                                                         |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Runtime                | Docker Compose, full-stack containers                                                                                                                                                  |
| Backend                | FastAPI, Pydantic, SQLAlchemy, NetworkX, LangGraph                                                                                                                                     |
| Frontend               | Next.js 16.2.4, React 19, TypeScript, TanStack Query, ECharts                                                                                                                          |
| Database               | PostgreSQL 16 for courses, file versions, chunks, graphs, QA sessions, and traces                                                                                                      |
| Vector Store           | Qdrant 1.17.1, collection `knowledge_chunks`                                                                                                                                         |
| Cache And Coordination | Redis 7                                                                                                                                                                                |
| Model API              | OpenAI-compatible Embedding / Chat API                                                                                                                                                 |
| Retrieval              | Layered retrieval: Query-Type-aware Fast / Standard / Deep Graph three-tier recall, Redis cache, fusion, rerank, then parent context assembly                                          |
| Graph                  | LLM candidates, chunk-vector semantic graph, graph algorithms for sparse construction, deduplication, communities, centrality, and hidden links; supports incremental and full rebuild |
| QA                     | Agentic RAG: Perception → Planning → Retrieval → EvidenceEvaluator → Generation, with cross-lingual retrieval and pre-generation evidence assessment                               |

## Technology Stack

| Layer                  | Technology                                                          | Role                                                                                                       |
| ---------------------- | ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Frontend               | Next.js 16.2.4, React 19, TypeScript, TanStack Query, ECharts       | Course management, upload and ingestion UI, search, QA, graph browsing, runtime settings                   |
| API                    | FastAPI, Pydantic, SQLAlchemy, LangGraph                            | REST / SSE APIs, typed validation, transaction orchestration, ingestion, retrieval, and QA orchestration   |
| Graph Algorithms       | NetworkX, NumPy, SciPy                                              | Sparse construction, connected components, Louvain, spectral clustering, centrality, Dijkstra hidden links |
| Database               | PostgreSQL 16                                                       | Courses, file versions, chunks, graphs, QA sessions, traces, and compensation records                      |
| Vector Search          | Qdrant 1.17.1                                                       | Parent / child chunk vectors, dense recall, vector health checks                                           |
| Lexical Search         | PostgreSQL text data, BM25                                          | Child chunk lexical recall and hybrid fusion                                                               |
| Cache And Coordination | Redis 7                                                             | Runtime cache, task coordination, service dependency                                                       |
| Parsing                | PyMuPDF, PPTX / DOCX / Markdown / HTML / Notebook parsers, OCR path | Convert heterogeneous course files into structured sections and text                                       |
| Model API              | OpenAI-compatible Embedding / Chat API                              | Embeddings, summaries, keywords, entity candidates, relation candidates, answer generation                 |
| Reranking              | Lightweight reranker, optional Cross-Encoder                        | Reorder fused candidates by relevance                                                                      |
| Deployment             | Docker Compose                                                      | Fixed service boundaries, dependency versions, local persistence                                           |
| Testing                | pytest, Vitest, Next build, Docker smoke                            | Behavioral regression, frontend/backend contracts, no-fallback quality gates                               |

## Core Capabilities

| Capability                   | Description                                                                                                                                      |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Multi-format parsing         | Supports PDF, PPT/PPTX, DOCX, Markdown, TXT, Notebook, HTML, and image materials                                                                 |
| Parent-child chunking        | Parent chunks keep full context; child chunks drive precise recall, reranking, and evidence citation                                             |
| Semantic chunking            | Long text is split by structure, semantic boundaries, sentence boundaries, and length limits; embedding similarity can assist boundary selection |
| Context-enriched vectors     | Embedding input includes file metadata, chapter, parent summary, neighboring child summaries, keywords, table markers, and formula markers       |
| Hybrid retrieval             | Qdrant child dense recall is fused with child BM25 recall before reranking                                                                       |
| Cross-lingual retrieval      | LLM translates queries into bilingual sub-queries; DocumentGrader uses embedding similarity to bridge language barriers                          |
| Graph enhancement            | Graph relations must link back to evidence chunks; the graph expands retrieval signals instead of replacing evidence                             |
| Graph-theoretic construction | Sparse graphs, communities, centrality, Dijkstra, and relation completion reduce noise and preserve key structure                                |
| Observable QA                | Retrieval audits, model-call audits, agent traces, citations, and failure reasons are stored                                                     |
| Runtime checks               | Health checks, runtime checks, fallback state, Qdrant status, and model endpoint status are exposed                                              |

## System Architecture

```mermaid
flowchart TB
    USER["User Browser"] --> WEB["Next.js Web<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["FastAPI<br/>course-kg-api"]

    subgraph APP["Application Layer"]
        API --> INGEST["Ingestion Pipeline<br/>parse -> parent/child chunk -> augment -> vector upsert"]
        API --> RETRIEVAL["Retrieval Pipeline<br/>dense/BM25 -> fusion -> rerank -> parent context"]
        API --> GRAPH["Graph Pipeline<br/>LLM candidates -> vector similarity graph -> sparse graph -> communities/centrality/inference"]
        API --> QA["Agentic QA<br/>Perception -> Planning -> Retrieval -> EvidenceEvaluator -> Generation"]
    end

    subgraph STORE["Storage And Runtime"]
        PG[("PostgreSQL<br/>metadata, chunks, sparse graph, audit records")]
        QD[("Qdrant<br/>chunk vectors and similarity recall")]
        RD[("Redis<br/>runtime cache, embedding / retrieval cache, task coordination")]
        FS["data/<br/>course files, parser artifacts, local persistence"]
    end

    subgraph MODEL["Model API"]
        LLM["OpenAI-compatible endpoint<br/>embedding / chat / graph extraction"]
    end

    API --> PG
    API --> QD
    API --> RD
    API --> FS
    API --> LLM
```

## Data Flow

```mermaid
sequenceDiagram
    participant User
    participant Web
    participant API
    participant Files as File Storage
    participant DB as PostgreSQL
    participant Vector as Qdrant
    participant Model as Model API

    User->>Web: Upload or select course files
    Web->>API: Create ingestion batch
    API->>DB: Create batch, job, document/version state
    API->>Files: Store original file and parser artifacts
    API->>API: Parse chapters, pages, tables, formulas, and Notebook cells
    API->>API: Create parent/child chunks and context-enriched text
    API->>Model: Generate summaries, keywords, and embeddings
    API->>Vector: Upsert active chunk vectors
    API->>DB: Activate document version and chunks
    API->>Model: Extract entity/relation candidates
    API->>API: Run sparse graph construction, communities, centrality, and inference
    API->>DB: Store concepts, relations, evidence, and graph-algorithm fields
    API->>DB: Incremental update: recompute only subgraphs tied to changed documents
    API-->>Web: Stream logs, progress, retry, and failure state over SSE
```

Ingestion uses explicit batch / job state and file-level locks. A course keeps at most one non-terminal ingestion batch at a time. PostgreSQL is the source of truth for lifecycle state; Qdrant and Redis are derived or runtime stores. Failures record compensation or actionable error context instead of silently degrading.

## Ingestion, Chunking, And Vectors

### Hierarchical Chunking

1. Parsers convert source files into `ParsedSection` objects while preserving chapter, page, source type, table, formula, Notebook cell, and image OCR metadata.
2. Each structured section creates a parent chunk that preserves the full section, page span, or natural semantic segment.
3. Parent chunks are split into child chunks for precise recall, reranking, and evidence localization.
4. Markdown and Notebook files prefer heading and cell hierarchy; ordinary long text uses semantic boundaries, sentence boundaries, and safe length limits.
5. When `SEMANTIC_CHUNKING_ENABLED=true` and text length reaches `SEMANTIC_CHUNKING_MIN_LENGTH`, embedding similarity can assist chunk boundary selection.

> **Design Intent (Why we do this)**: Fixed-size chunking leads to severe context fragmentation. Using a parent-child hierarchy and semantic chunking ensures the model leverages the high precision of child chunks during retrieval, while accessing the full context of parent chunks during generation. This completely decouples the "retrieval unit" from the "generation unit".

### Context-Enriched Embeddings

Child vectors are not built from child text alone. `contextual_embedding_text()` builds context-enriched input:

```text
file metadata
chapter, page, and source type
child chunk content
parent summary or parent content
neighboring child summaries
keywords
table, formula, and content-kind markers
```

Parent chunks keep their own text, summary, and keywords. Child chunks inherit parent semantic summaries and neighboring context, reducing context loss in fine-grained chunks. The current embedding text version is `contextual_enriched_v2`.

> **Design Intent (Why we do this)**: Isolated short text chunks easily suffer from semantic ambiguity when embedded alone (e.g., "this method", "the next step"). Forcing the injection of parent summaries and neighboring context before embedding acts as "contextual retrieval" at ingestion time, significantly improving recall accuracy in the dense retrieval stage.

### Deduplication And Idempotency

Ingestion detects duplicates by course, normalized title, and checksum. Unchanged files are skipped with `unchanged_checksum`; duplicate copies with the same normalized title and checksum are skipped with `duplicate_document`, avoiding duplicate chunks and vectors. Forced reingestion regenerates document versions, chunks, Qdrant vectors, and graph candidates.

## Graph Construction

DialoGraph builds graphs with an evidence-first policy: the LLM produces candidate entities and explicit relations, while chunk vectors and graph algorithms decide the final structure. PostgreSQL is the source of truth for the sparse graph; Qdrant provides chunk vectors and similarity signals.

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

### 1. Entities And Evidence

Each concept stores a canonical name, aliases, chapter references, importance, and evidence chunk count. Concept vectors are not generated from names. They are centroids of supporting chunk vectors:

$$
\mathbf{v}_e = \frac{1}{|C_e|}\sum_{c \in C_e}\mathbf{v}_c
$$

$C_e$ is the set of active child chunks supporting entity $e$. The centroid is normalized before semantic graph construction.

> **Design Intent (Why we do this)**: Traditional GraphRAG directly embeds the extracted concept name, which biases the vector space toward the LLM's generic pre-training data. Calculating the centroid of all supporting underlying child chunk vectors ensures the graph remains perfectly faithful to the specific local context of the course, eliminating concept drift.

### 2. Dynamic R-NN + K-NN Sparse Graph

Each concept dynamically chooses outgoing candidates from its evidence volume:

$$
K_i = \mathrm{clamp}\bigl(4 + \lfloor \log_2(1 + m_i) \rfloor,\, 4,\, 12\bigr)
$$

Each concept dynamically limits accepted reciprocal candidates from chapter coverage:

$$
R_i = \mathrm{clamp}\bigl(2 + \lfloor \log_2(1 + r_i) \rfloor,\, 2,\, 8\bigr)
$$

$m_i$ is evidence chunk count and $r_i$ is chapter reference count. The system keeps mutual nearest neighbors, candidates accepted by the reciprocal cap, and high-confidence explicit LLM relations, keeping edge count close to linear in node count.

> **Design Intent (Why we do this)**: If high-frequency words (e.g., "algorithm", "data") accept edges without limits, the graph quickly collapses into a useless giant hub (the Hubness Problem). A dynamic bidirectional limit algorithm based on evidence volume and chapter coverage mathematically squeezes out low-quality edges, guaranteeing the graph remains clear, sparse, and focused.

### 3. Edge Weights And Graph Algorithms

Edge weight combines LLM confidence, semantic similarity, evidence support, and structural consistency:

$$
w_{ij}=0.45\,c_{ij}^{\mathrm{llm}}+0.30\,s_{ij}^{\mathrm{sem}}+0.15\,s_{ij}^{\mathrm{evidence}}+0.10\,s_{ij}^{\mathrm{structure}}
$$

When no explicit LLM relation exists, $c_{ij}^{\mathrm{llm}}=0$. The final $w_{ij}$ is clipped to $[0,1]$. The graph stage runs:

- Connected-component ablation: removes isolated, low-evidence, low-importance noise while preserving enough course nodes.
- Louvain community detection: primary community labels and frontend color groups.
- Spectral clustering: secondary partitions for large components and large communities.
- Centrality: degree, weighted degree, PageRank, betweenness, closeness, and a combined `centrality_score`.
- Graph simplification: keeps central nodes, community representatives, bridge edges, and high-evidence concepts.

> **Design Intent (Why we do this)**: LLM-extracted graphs are often noisy and fragmented. Introducing classic graph algorithms like connected components, Louvain community detection, and centrality is the most effective way to hedge against LLM hallucinations. This multi-dimensional graph ablation retains only high-value core structures, solving the notorious "hairball" visualization problem in large-scale graphs.

### 4. Hidden Links And Relation Completion

Dijkstra searches 2-3 hop hidden relations on a non-negative cost graph:

$$
\mathrm{cost}_{ij}=\frac{1}{0.05+w_{ij}}
$$

If endpoint semantic similarity is high and path cost is low, the system writes a `relates_to` edge with `relation_source="dijkstra_inferred"` and uses the path score to repair weak existing weights. The system then extracts evidence snippets from two-hop neighborhoods around high-centrality nodes and asks the LLM to complete only evidence-supported relations.

The frontend colors graph nodes by Louvain community, sizes nodes by centrality and graph rank, and renders inferred edges as dashed lines. Users can filter communities and open key entity details quickly.

> **Design Intent (Why we do this)**: True knowledge often spans chapters (e.g., A belongs to B, B contains C, so A relates to C, even if unstated). Using Dijkstra's algorithm to efficiently find structural holes and then using the LLM to verify these specific 2-hop evidence snippets enables automated, highly precise ontology expansion that surpasses traditional rule-based extraction.

## Retrieval And QA

DialoGraph's QA pipeline uses a **Perception → Planning → Retrieval → EvidenceEvaluator → Generation** five-stage agent architecture orchestrated by LangGraph. Every node writes to `agent_trace_events`, and the frontend renders the live trace via SSE.

```mermaid
flowchart LR
    Q["Question"] --> PER["Perception<br/>intent · entity extraction · graph concept matching"]
    PER -->|"greeting / clarify"| AG["AnswerGenerator"]
    PER -->|"needs retrieval"| PLAN["RetrievalPlanner<br/>strategy selection · cross-lingual translation"]
    PLAN --> RET["RetrievalExecutor<br/>global_dense / local_graph / hybrid / community"]
    RET --> GRADE["DocumentGrader<br/>0.4·overlap + 0.6·embedding_sim"]
    GRADE --> EVAL["EvidenceEvaluator<br/>pre-generation sufficiency check"]
    EVAL -->|"insufficient + retry<2"| PLAN
    EVAL -->|"sufficient / insufficient+retry≥2"| CS["ContextSynthesizer"]
    CS --> AG
    AG --> CC["CitationChecker"]
    CC --> CV["CitationVerifier"]
    CV --> REFL["Reflection<br/>post-generation (default off)"]
    REFL --> AC["AnswerCorrector"]
    AC --> CS
```

### Perception

The Perception node understands user intent, extracts entities, and matches them against the course graph:

1. **Fast-path**: greetings route to `direct_answer`; empty or anaphoric queries route to `clarify`.
2. **LLM perception**: calls ChatProvider to classify intent (`definition` / `comparison` / `analysis` / `application` / `procedure`), extract entities, and generate sub-queries.
3. **Graph concept matching**: matches extracted entities against `concepts` and `concept_aliases`, retrieving matched concept communities and one-hop neighbors.

Perception outputs:

- `intent`: question type
- `entities` / `matched_concepts`: extracted entities and graph matches
- `perceived_communities`: relevant community IDs
- `suggested_strategy`: recommended strategy (`global_dense`, `local_graph`, `hybrid`, `community`)
- `needs_graph`: whether graph enhancement is needed

### RetrievalPlanner

The planning layer selects a retrieval strategy based on Perception output and performs cross-lingual query translation:

**Strategy selection:**

| Intent                                 | Condition              | Strategy                                |
| -------------------------------------- | ---------------------- | --------------------------------------- |
| `definition`                         | `needs_graph=false`  | `global_dense` (pure dense + BM25)    |
| `comparison` or `needs_graph=true` | —                     | `hybrid` (layered hybrid retrieval)   |
| `application` / `procedure`        | matched concepts exist | `local_graph` (local graph search)    |
| `analysis`                           | matched concepts ≥ 3  | `community` (community-scoped search) |

**Cross-lingual query expansion:**

The system detects query language (Chinese / English) and uses LLM to translate to the opposite language:

$$
Q_{\mathrm{bilingual}} = \{q_{\mathrm{original}},\; q_{\mathrm{translated}}\} \cup Q_{\mathrm{sub}}
$$

After deduplication, all sub-queries enter the RetrievalExecutor. This allows a Chinese query like "最大流" to also match English course materials via the translated sub-query "max flow".

> **Design Intent (Why we do this)**: Multilingual embedding models often struggle with cross-lingual alignment. Explicitly translating queries and including bilingual sub-queries allows the retrieval engine to probe the document store in multiple linguistic forms simultaneously. This is a much more robust engineering solution than relying solely on the embedding model's internal alignment.

### RetrievalExecutor

The execution layer dispatches to different retrieval backends based on strategy:

| Strategy             | Backend                                               | Description                                                                                                                                                  |
| -------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `global_dense`     | `hybrid_search_chunks`                              | Pure dense + BM25 hybrid recall                                                                                                                              |
| `local_graph`      | `local_graph_search`                                | Uses Perception-matched concepts as seeds, recalls evidence chunks from seed concepts and their 1-hop neighbors, then merges with base dense recall          |
| `community`        | `community_search_chunks`                           | Restricts to perceived Louvain communities, blends dense recall with community-concept evidence chunks, and applies a +0.15 score boost inside the community |
| `hybrid` (default) | `layered_search_chunks` / `graph_enhanced_search` | Query-Type layer routing: Layer 1 Fast / Layer 2 Standard / Layer 3 Deep Graph                                                                               |

All strategies follow the **Small-to-Big** principle: only child chunks enter recall and reranking; parent context is assembled later via `parent_chunk_id`.

### DocumentGrader

Grades recalled documents for admission, fusing lexical overlap and vector semantic similarity:

$$
\mathrm{grade\_score} = 0.40 \cdot r_{\mathrm{overlap}} + 0.60 \cdot s_{\mathrm{embedding}}
$$

Where:

- $r_{\mathrm{overlap}} = \dfrac{|T_q \cap T_d|}{|T_q|}$, with $T_q$ the query term set and $T_d$ the document title+snippet+content term set
- $s_{\mathrm{embedding}}$ is the cosine similarity between query and document vectors; when the raw vector is unavailable, it falls back to the dense score recorded at retrieval time

Admission rules (pass if any holds):

$$
\begin{cases}
\mathrm{grade\_score} \ge 0.35 & \text{(primary gate)} \\
s_{\mathrm{embedding}} \ge 0.45 & \text{(cross-lingual bridge gate)} \\
r_{\mathrm{overlap}} \ge 0.25 \;\land\; \mathrm{original\_score} \ge 0.3 & \text{(auxiliary gate)}
\end{cases}
$$

The cross-lingual bridge gate solves a critical problem: a Chinese query "最大流" and English material "max flow" share weak overlap in the `text-embedding-v4` vector space, but LLM-translated sub-queries can recall relevant chunks via dense search. In such cases $r_{\mathrm{overlap}}$ may be near zero while $s_{\mathrm{embedding}}$ remains high; the bridge gate prevents these valid cross-lingual results from being killed by monolingual term matching.

> **Design Intent (Why we do this)**: This is a funnel specifically designed to break the "cross-lingual wall". A Chinese query and English material often share zero literal overlap but high semantic relevance. The $s_{\mathrm{embedding}} \ge 0.45$ cross-lingual bridge gate acts as an exemption channel, elegantly preventing purely lexical (BM25) mismatch from killing valid cross-lingual results.

### EvidenceEvaluator

**Before answer generation**, the EvidenceEvaluator assesses whether retrieved evidence is sufficient. This is DialoGraph's **pre-generation reflection** mechanism:

For each graded document, extract `grade_score` and compute:

$$
\bar{g} = \frac{1}{n}\sum_{i=1}^{n} g_i,\qquad g_{\max} = \max_i g_i
$$

Intent-dependent minimum evidence thresholds:

$$
\begin{cases}
(n_{\min}, \bar{g}_{\min}) = (1,\, 0.25) & \text{if intent} \in \{\text{definition},\, \text{procedure}\} \\
(n_{\min}, \bar{g}_{\min}) = (2,\, 0.20) & \text{if intent} \in \{\text{comparison},\, \text{analysis}\} \\
(n_{\min}, \bar{g}_{\min}) = (1,\, 0.20) & \text{otherwise}
\end{cases}
$$

Sufficiency condition:

$$
\mathrm{sufficient} \;\Leftrightarrow\; g_{\max} \ge 0.35 \;\land\; n \ge n_{\min} \;\land\; \bar{g} \ge \bar{g}_{\min}
$$

If only an anchor exists but quantity/score is marginal, the run is marked `marginal` and generation proceeds. If evidence is insufficient and `retry_count < 2`, the flow routes back to `RetrievalPlanner` with doubled `top_k`. If `retry_count >= 2`, `low_evidence=true` is set and generation proceeds with a disclaimer in the prompt and no forced citations.

> **Design Intent (Why we do this)**: This breaks the flawed traditional RAG paradigm of "generate answers no matter what garbage was retrieved". As a defensive assessment layer, it gives the system the ability to "know what it doesn't know". Intercepting low-quality retrievals and failing gracefully is crucial for reliability in professional domain QA.

### Post-Generation Loop (Default Off)

`ENABLE_POST_GENERATION_REFLECTION=false` by default. When enabled, post-generation nodes execute:

- **CitationVerifier**: samples high-importance claims for NLI verification.
- **Reflection**: LLM evaluates the answer for hallucination, insufficient coverage, or contradiction, returning `has_issue` / `issue_type` / `suggestion`.
- **AnswerCorrector**: adjusts strategy based on reflection results (expand top_k, rewrite query, or regenerate from high-confidence documents).

These nodes are observable in traces but do not participate in the main loop by default, avoiding extra latency and model call costs. The pre-generation `EvidenceEvaluator` already covers most insufficient-evidence scenarios.

### Layered Retrieval

The system automatically selects retrieval depth based on Query Type and routing:

| Layer              | Trigger                       | Description                                                                                     |
| ------------------ | ----------------------------- | ----------------------------------------------------------------------------------------------- |
| Layer 1 Fast       | definition / formula queries  | Redis cache preferred, dense recall only, BM25 skipped                                          |
| Layer 2 Standard   | example / procedure / default | Existing dense + BM25 hybrid, fused then reranked                                               |
| Layer 3 Deep Graph | comparison / multi-hop        | Hybrid + graph v2 enhancement: centrality boost, community aggregation, Dijkstra path expansion |

Embeddings and retrieval results are cached in Redis with TTL bound to embedding text version and the course's latest document version.

### Small-To-Big Retrieval

The main retrieval path sends only child chunks through recall and reranking, then attaches parent context:

```text
child dense recall + child BM25 recall
-> weighted fusion
-> rerank
-> load parent_chunk_id
-> child evidence + parent context + citations
```

This avoids both coarse recall from overly large chunks and missing context from tiny chunks. Retrieval results carry `retrieval_granularity=child_with_parent_context`, dense score, BM25 score, fused score, rerank score, graph boost, and model audit fields.

## Technical Advantages

| Advantage                  | Detail                                                                                                                                             |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Evidence-first             | Answers, relations, and graph expansion return to real chunks and parent context                                                                   |
| Context and precision      | Child chunks provide precise recall; parent chunks provide complete explanation context                                                            |
| Controlled graph structure | R-NN + K-NN caps edge growth, while components and communities reduce noise                                                                        |
| Course-material aware      | Preserves chapters, pages, formulas, tables, Notebook cells, and source types                                                                      |
| Auditable                  | Stores batch/job/log state, model calls, retrieval scores, fallback state, and citations                                                           |
| Recoverable                | PostgreSQL stores lifecycle state; Qdrant / Redis can be repaired from durable records                                                             |
| No silent degradation      | Missing models, database, or Qdrant fail fast with actionable error context                                                                        |
| Extensible                 | Reranking, semantic chunking, graph enhancement, and model endpoints are isolated by configuration and service layers                              |
| Clear agent architecture   | Perception-Planning-Retrieval-EvidenceEvaluator-Generation separation; each stage independently observable and tunable                             |
| Cross-lingual robustness   | LLM translation query expansion + embedding similarity bridge + cross-lingual admission gate mitigates monolingual embedding alignment limitations |

## Data Model

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

| Table                                                     | Purpose                                                                                                          |
| --------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `courses`                                               | Course workspace                                                                                                 |
| `documents` / `document_versions`                     | File metadata, versions, and parser artifact paths                                                               |
| `chunks`                                                | Parent/child text chunks, summaries, keywords, embedding text version, and evidence text                         |
| `concepts`                                              | Concepts, chapter references, evidence counts, communities, centrality, and graph rank                           |
| `concept_aliases`                                       | Concept aliases and normalized aliases                                                                           |
| `concept_relations`                                     | Sparse edges, relation types, evidence chunks, weights, semantic similarity, support count, and inference source |
| `ingestion_batches` / `ingestion_jobs`                | Batch ingestion and single-file jobs                                                                             |
| `ingestion_logs` / `ingestion_compensation_logs`      | Event streams and cross-store compensation records                                                               |
| `qa_sessions` / `agent_runs` / `agent_trace_events` | QA sessions, agent runs, and observable traces                                                                   |

## Configuration

Copy the configuration template:

```powershell
Copy-Item .env.example .env
```

Common variables:

| Variable                                                                    | Description                                                                                        |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `API_HOST_PORT` / `WEB_HOST_PORT`                                       | Host ports                                                                                         |
| `DATABASE_URL`                                                            | PostgreSQL connection URL                                                                          |
| `ENABLE_DATABASE_FALLBACK`                                                | Database fallback switch, default `false`                                                        |
| `QDRANT_URL` / `QDRANT_COLLECTION`                                      | Qdrant URL and collection name                                                                     |
| `REDIS_URL`                                                               | Redis URL                                                                                          |
| `COURSE_NAME`                                                             | Default course name                                                                                |
| `DATA_ROOT`                                                               | Local data root                                                                                    |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL`                                    | OpenAI-compatible model endpoint                                                                   |
| `OPENAI_RESOLVE_IP`                                                       | Target IP when model-domain resolution must be pinned                                              |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` / `EMBEDDING_BATCH_SIZE`   | Embedding model, dimensions, and batch size                                                        |
| `CHAT_MODEL`                                                              | Chat and graph extraction model                                                                    |
| `GRAPH_EXTRACTION_CHUNK_LIMIT` / `GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT` | Graph extraction chunk cap and per-document sampling cap                                           |
| `ENABLE_MODEL_FALLBACK`                                                   | Model fallback switch, default `false`                                                           |
| `RERANKER_ENABLED` / `RERANKER_MODEL` / `RERANKER_MAX_LENGTH`         | Cross-Encoder reranker settings                                                                    |
| `SEMANTIC_CHUNKING_ENABLED` / `SEMANTIC_CHUNKING_MIN_LENGTH`            | Semantic chunking switch and minimum text length                                                   |
| `RETRIEVAL_LAYER_ENABLED`                                                 | Retrieval layer switch, default `true`                                                           |
| `RETRIEVAL_CACHE_TTL_SECONDS`                                             | Redis retrieval cache TTL, default `300`                                                         |
| `ENABLE_AGENTIC_REFLECTION`                                               | Agentic reflection and correction master switch, default `true`                                  |
| `ENABLE_POST_GENERATION_REFLECTION`                                       | Post-generation reflection switch (CitationVerifier/Reflection/AnswerCorrector), default `false` |
| `CITATION_VERIFICATION_SAMPLE_MAX`                                        | Citation verification sample size per answer, default `3`                                        |
| `REFLECTION_MAX_RETRIES`                                                  | Max reflection-triggered correction retries, default `2`                                         |
| `MODEL_BRIDGE_ENABLED` / `MODEL_BRIDGE_PORT`                            | Host model-bridge switch and port                                                                  |

Docker Compose overrides infrastructure URLs inside the API container:

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/course_kg
QDRANT_URL=http://qdrant:6333
REDIS_URL=redis://redis:6379/0
```

If the host can reach a model provider but container networking to that provider is unstable, enable the model bridge. The bridge forwards the real OpenAI-compatible endpoint only; it does not generate fake responses and is not a fallback path.

## Running

1. Configure `.env` with a real model endpoint:

```env
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

2. Start the Docker stack:

```powershell
docker compose -f infra/docker-compose.yml up -d api web postgres redis qdrant
```

Windows users can also double-click `start-app.bat` to launch the backend, frontend, and infrastructure containers. This script **does not** force an image rebuild, making it suitable for daily quick starts.

If the application code or dependencies have changed and you need to rebuild the local images, run:

```powershell
docker compose -f infra/docker-compose.yml build api web
```

Or on Windows simply run `rebuild-images.bat`. To force a rebuild without cache, use `rebuild-images.bat -NoCache`.

3. Open the web app:

```text
http://127.0.0.1:3000
```

## Validation

Backend tests:

```powershell
docker exec course-kg-api python -m pytest tests
```

Frontend checks:

```powershell
npm run typecheck --workspace web
npm run lint --workspace web
npm run test --workspace web
```

Docker smoke:

```powershell
python scripts/docker_smoke.py --base-url http://127.0.0.1:8000/api
```

Course quality gate:

```powershell
docker exec course-kg-api python /app/scripts/quality_gate.py --course-name "Course Name"
```

Reingest one course and clean stale derived data:

```powershell
docker exec course-kg-api python /app/scripts/reingest_all_courses.py --course-name "Course Name" --cleanup-stale
```

Validation focus:

| Check                   | Expected                                                                                                                                                                                 |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Health                  | `/api/health` returns available service status                                                                                                                                         |
| Runtime configuration   | `/api/settings/runtime-check` has no blocking issue                                                                                                                                    |
| Model fallback          | `ENABLE_MODEL_FALLBACK=false`; model outages fail fast                                                                                                                                 |
| Database fallback       | `ENABLE_DATABASE_FALLBACK=false`; database outages fail fast                                                                                                                           |
| Vector health           | Qdrant vector count matches active chunks and no zero vectors exist                                                                                                                      |
| Retrieval quality       | Child recall, parent context, rerank, and citation fields are complete                                                                                                                   |
| Graph quality           | Node count meets the retention floor, edge growth is near-linear, and community, centrality, and weight fields are populated; graph remains stable after incremental updates             |
| Layered retrieval       | Different query types hit the correct layer; Redis cache hit/miss behaves correctly                                                                                                      |
| Agentic loop            | Perception, RetrievalPlanner, EvidenceEvaluator nodes are observable in traces; post-generation Reflection is off by default; LLM errors are not silently swallowed when fallback is off |
| Cross-lingual retrieval | Mixed Chinese-English queries hit materials in the opposite language; DocumentGrader bridge gate is active                                                                               |
| Log observability       | Ingestion logs expose progress, retry, failure reason, and terminal event                                                                                                                |

## Version Control Rules

Excluded from Git:

- `.env`, local secrets, Authorization headers, and provider responses.
- `data/`, `output/`, `models/`, and `comparative_experiment/` runtime data.
- `node_modules/`, `.next/`, `dist/`, `build/`, coverage, and Playwright reports.
- `.db`, `.sqlite*`, `__pycache__/`, `*.pyc`, `*.tsbuildinfo`, logs, and temporary files.

Tracked in Git:

- `apps/api`, `apps/web`, `packages/shared`, `scripts`, and `infra`.
- README files, `.env.example`, Docker configuration, tests, schemas, and shared type contracts.
