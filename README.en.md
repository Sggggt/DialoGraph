**English** | [中文](./README.md)

<p align="center">
  <img src="./assets/diagraph-logo.svg" alt="DialoGraph logo" width="132" height="132">
</p>

<h1 align="center">DialoGraph</h1>

DialoGraph is a Dockerized knowledge infrastructure system for local course materials. It parses PDFs, slides, documents, web pages, notebooks, images, and Markdown into searchable text chunks, Qdrant vectors, PostgreSQL sparse knowledge graphs, and citation-backed answers.

The default runtime uses real PostgreSQL, Qdrant, Redis, and an OpenAI-compatible model API. Model fallback and database fallback are disabled by default; production-quality validation does not use zero vectors, fake embeddings, local JSON retrieval, or extractive substitute answers.

## At A Glance

| Area | Implementation |
| --- | --- |
| Runtime | Docker Compose, full-stack containers |
| Backend | FastAPI, Pydantic, SQLAlchemy, NetworkX |
| Frontend | Next.js 16.2.4, React 19, TypeScript, TanStack Query, ECharts |
| Database | PostgreSQL 16 for courses, file versions, chunks, graphs, QA sessions, and traces |
| Vector Store | Qdrant 1.17.1, collection `knowledge_chunks` |
| Cache And Coordination | Redis 7 |
| Model API | OpenAI-compatible Embedding / Chat API |
| Retrieval | Child chunk dense + BM25 recall, fusion, rerank, then parent context assembly |
| Graph | LLM candidates, chunk-vector semantic graph, graph algorithms for sparse construction, deduplication, communities, centrality, and hidden links |

## Technology Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Frontend | Next.js 16.2.4, React 19, TypeScript, TanStack Query, ECharts | Course management, upload and ingestion UI, search, QA, graph browsing, runtime settings |
| API | FastAPI, Pydantic, SQLAlchemy | REST / SSE APIs, typed validation, transaction orchestration, ingestion, retrieval, and QA orchestration |
| Graph Algorithms | NetworkX, NumPy, SciPy | Sparse construction, connected components, Louvain, spectral clustering, centrality, Dijkstra hidden links |
| Database | PostgreSQL 16 | Courses, file versions, chunks, graphs, QA sessions, traces, and compensation records |
| Vector Search | Qdrant 1.17.1 | Parent / child chunk vectors, dense recall, vector health checks |
| Lexical Search | PostgreSQL text data, BM25 | Child chunk lexical recall and hybrid fusion |
| Cache And Coordination | Redis 7 | Runtime cache, task coordination, service dependency |
| Parsing | PyMuPDF, PPTX / DOCX / Markdown / HTML / Notebook parsers, OCR path | Convert heterogeneous course files into structured sections and text |
| Model API | OpenAI-compatible Embedding / Chat API | Embeddings, summaries, keywords, entity candidates, relation candidates, answer generation |
| Reranking | Lightweight reranker, optional Cross-Encoder | Reorder fused candidates by relevance |
| Deployment | Docker Compose | Fixed service boundaries, dependency versions, local persistence |
| Testing | pytest, Vitest, Next build, Docker smoke | Behavioral regression, frontend/backend contracts, no-fallback quality gates |

## Core Capabilities

| Capability | Description |
| --- | --- |
| Multi-format parsing | Supports PDF, PPT/PPTX, DOCX, Markdown, TXT, Notebook, HTML, and image materials |
| Parent-child chunking | Parent chunks keep full context; child chunks drive precise recall, reranking, and evidence citation |
| Semantic chunking | Long text is split by structure, semantic boundaries, sentence boundaries, and length limits; embedding similarity can assist boundary selection |
| Context-enriched vectors | Embedding input includes file metadata, chapter, parent summary, neighboring child summaries, keywords, table markers, and formula markers |
| Hybrid retrieval | Qdrant child dense recall is fused with child BM25 recall before reranking |
| Graph enhancement | Graph relations must link back to evidence chunks; the graph expands retrieval signals instead of replacing evidence |
| Graph-theoretic construction | Sparse graphs, communities, centrality, Dijkstra, and relation completion reduce noise and preserve key structure |
| Observable QA | Retrieval audits, model-call audits, agent traces, citations, and failure reasons are stored |
| Runtime checks | Health checks, runtime checks, fallback state, Qdrant status, and model endpoint status are exposed |

## System Architecture

```mermaid
flowchart TB
    USER["User Browser"] --> WEB["Next.js Web<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["FastAPI<br/>course-kg-api"]

    subgraph APP["Application Layer"]
        API --> INGEST["Ingestion Pipeline<br/>parse -> parent/child chunk -> augment -> vector upsert"]
        API --> RETRIEVAL["Retrieval Pipeline<br/>dense/BM25 -> fusion -> rerank -> parent context"]
        API --> GRAPH["Graph Pipeline<br/>LLM candidates -> vector similarity graph -> sparse graph -> communities/centrality/inference"]
        API --> QA["Observable QA<br/>route -> retrieve -> grade evidence -> answer -> citation check"]
    end

    subgraph STORE["Storage And Runtime"]
        PG[("PostgreSQL<br/>metadata, chunks, sparse graph, audit records")]
        QD[("Qdrant<br/>chunk vectors and similarity recall")]
        RD[("Redis<br/>runtime cache and task coordination")]
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

### 2. Dynamic R-NN + K-NN Sparse Graph

Each concept dynamically chooses outgoing candidates from its evidence volume:

$$
K_i = \mathrm{clamp}(4 + \lfloor \log_2(1 + m_i) \rfloor, 4, 12)
$$

Each concept dynamically limits accepted reciprocal candidates from chapter coverage:

$$
R_i = \mathrm{clamp}(2 + \lfloor \log_2(1 + r_i) \rfloor, 2, 8)
$$

$m_i$ is evidence chunk count and $r_i$ is chapter reference count. The system keeps mutual nearest neighbors, candidates accepted by the reciprocal cap, and high-confidence explicit LLM relations, keeping edge count close to linear in node count.

### 3. Edge Weights And Graph Algorithms

Edge weight combines LLM confidence, semantic similarity, evidence support, and structural consistency:

$$
w_{ij}=0.45c_{ij}^{llm}+0.30s_{ij}^{sem}+0.15s_{ij}^{evidence}+0.10s_{ij}^{structure}
$$

When no explicit LLM relation exists, $c_{ij}^{llm}=0$. The final $w_{ij}$ is clipped to $[0,1]$. The graph stage runs:

- Connected-component ablation: removes isolated, low-evidence, low-importance noise while preserving enough course nodes.
- Louvain community detection: primary community labels and frontend color groups.
- Spectral clustering: secondary partitions for large components and large communities.
- Centrality: degree, weighted degree, PageRank, betweenness, closeness, and a combined `centrality_score`.
- Graph simplification: keeps central nodes, community representatives, bridge edges, and high-evidence concepts.

### 4. Hidden Links And Relation Completion

Dijkstra searches 2-3 hop hidden relations on a non-negative cost graph:

$$
cost_{ij}=\frac{1}{0.05+w_{ij}}
$$

If endpoint semantic similarity is high and path cost is low, the system writes a `relates_to` edge with `relation_source="dijkstra_inferred"` and uses the path score to repair weak existing weights. The system then extracts evidence snippets from two-hop neighborhoods around high-centrality nodes and asks the LLM to complete only evidence-supported relations.

The frontend colors graph nodes by Louvain community, sizes nodes by centrality and graph rank, and renders inferred edges as dashed lines. Users can filter communities and open key entity details quickly.

## Retrieval And QA

```mermaid
flowchart LR
    Q["Question"] --> DENSE["child dense recall"]
    Q --> BM25["child BM25 recall"]
    DENSE --> FUSION["weighted fusion"]
    BM25 --> FUSION
    FUSION --> RERANK["rerank"]
    RERANK --> PARENT["load parent_chunk_id"]
    PARENT --> ANSWER["answer with citations"]
    Q -. "multi-hop / relation intent" .-> GRAPH["graph-enhanced evidence expansion"]
    GRAPH --> RERANK
```

Retrieval recalls child chunks by default, avoiding parent/child competition inside one candidate pool. Final results attach parent context through `parent_chunk_id` and keep dense, BM25, fused, rerank, graph boost, and model audit metadata.

Graph-enhanced retrieval does not replace text evidence. It starts from retrieved chunks, finds related concepts and relations, merges relation evidence chunks back into candidates, then reranks and cites through the same answer path.

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

### Agent QA

The QA path is split into observable nodes:

```text
question analysis -> routing -> query rewriting -> retrieval -> evidence grading -> context synthesis -> answer generation -> citation check -> self-check
```

Each node writes `agent_trace_events`, including node name, status, input/output summaries, candidate documents, scores, duration, and error information. Answers must include real chunk citations; the graph only enhances evidence candidates and does not produce unsupported conclusions.

## Technical Advantages

| Advantage | Detail |
| --- | --- |
| Evidence-first | Answers, relations, and graph expansion return to real chunks and parent context |
| Context and precision | Child chunks provide precise recall; parent chunks provide complete explanation context |
| Controlled graph structure | R-NN + K-NN caps edge growth, while components and communities reduce noise |
| Course-material aware | Preserves chapters, pages, formulas, tables, Notebook cells, and source types |
| Auditable | Stores batch/job/log state, model calls, retrieval scores, fallback state, and citations |
| Recoverable | PostgreSQL stores lifecycle state; Qdrant / Redis can be repaired from durable records |
| No silent degradation | Missing models, database, or Qdrant fail fast with actionable error context |
| Extensible | Reranking, semantic chunking, graph enhancement, and model endpoints are isolated by configuration and service layers |

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

| Table | Purpose |
| --- | --- |
| `courses` | Course workspace |
| `documents` / `document_versions` | File metadata, versions, and parser artifact paths |
| `chunks` | Parent/child text chunks, summaries, keywords, embedding text version, and evidence text |
| `concepts` | Concepts, chapter references, evidence counts, communities, centrality, and graph rank |
| `concept_aliases` | Concept aliases and normalized aliases |
| `concept_relations` | Sparse edges, relation types, evidence chunks, weights, semantic similarity, support count, and inference source |
| `ingestion_batches` / `ingestion_jobs` | Batch ingestion and single-file jobs |
| `ingestion_logs` / `ingestion_compensation_logs` | Event streams and cross-store compensation records |
| `qa_sessions` / `agent_runs` / `agent_trace_events` | QA sessions, agent runs, and observable traces |

## Configuration

Copy the configuration template:

```powershell
Copy-Item .env.example .env
```

Common variables:

| Variable | Description |
| --- | --- |
| `API_HOST_PORT` / `WEB_HOST_PORT` | Host ports |
| `DATABASE_URL` | PostgreSQL connection URL |
| `ENABLE_DATABASE_FALLBACK` | Database fallback switch, default `false` |
| `QDRANT_URL` / `QDRANT_COLLECTION` | Qdrant URL and collection name |
| `REDIS_URL` | Redis URL |
| `COURSE_NAME` | Default course name |
| `DATA_ROOT` | Local data root |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI-compatible model endpoint |
| `OPENAI_RESOLVE_IP` | Target IP when model-domain resolution must be pinned |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` / `EMBEDDING_BATCH_SIZE` | Embedding model, dimensions, and batch size |
| `CHAT_MODEL` | Chat and graph extraction model |
| `GRAPH_EXTRACTION_CHUNK_LIMIT` / `GRAPH_EXTRACTION_CHUNKS_PER_DOCUMENT` | Graph extraction chunk cap and per-document sampling cap |
| `ENABLE_MODEL_FALLBACK` | Model fallback switch, default `false` |
| `RERANKER_ENABLED` / `RERANKER_MODEL` / `RERANKER_MAX_LENGTH` | Cross-Encoder reranker settings |
| `SEMANTIC_CHUNKING_ENABLED` / `SEMANTIC_CHUNKING_MIN_LENGTH` | Semantic chunking switch and minimum text length |
| `MODEL_BRIDGE_ENABLED` / `MODEL_BRIDGE_PORT` | Host model-bridge switch and port |

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

| Check | Expected |
| --- | --- |
| Health | `/api/health` returns available service status |
| Runtime configuration | `/api/settings/runtime-check` has no blocking issue |
| Model fallback | `ENABLE_MODEL_FALLBACK=false`; model outages fail fast |
| Database fallback | `ENABLE_DATABASE_FALLBACK=false`; database outages fail fast |
| Vector health | Qdrant vector count matches active chunks and no zero vectors exist |
| Retrieval quality | Child recall, parent context, rerank, and citation fields are complete |
| Graph quality | Node count meets the retention floor, edge growth is near-linear, and community, centrality, and weight fields are populated |
| Log observability | Ingestion logs expose progress, retry, failure reason, and terminal event |

## Version Control Rules

Excluded from Git:

- `.env`, local secrets, Authorization headers, and provider responses.
- `data/`, `output/`, `models/`, and `comparative_experiment/` runtime data.
- `node_modules/`, `.next/`, `dist/`, `build/`, coverage, and Playwright reports.
- `.db`, `.sqlite*`, `__pycache__/`, `*.pyc`, `*.tsbuildinfo`, logs, and temporary files.

Tracked in Git:

- `apps/api`, `apps/web`, `packages/shared`, `scripts`, and `infra`.
- README files, `.env.example`, Docker configuration, tests, schemas, and shared type contracts.
