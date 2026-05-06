**English** | [Chinese](./README.md)

<p align="center">
  <img src="./assets/diagraph-logo.svg" alt="DialoGraph logo" width="132" height="132">
</p>

<h1 align="center">DialoGraph</h1>

A Dockerized knowledge-base system for local course materials. DialoGraph parses PDFs, slides, documents, web pages, notebooks, and images into searchable chunks, vector indexes, concept graphs, and citation-backed answers.

> The default stack runs on real PostgreSQL, Qdrant, Redis, and an OpenAI-compatible model API. Model fallback and database fallback are disabled by default; quality evaluation and production runs do not use fake embeddings, extractive substitute answers, or local JSON retrieval substitutes.

## At A Glance

| Area | Current Implementation |
| --- | --- |
| Runtime | Docker Compose, full-stack containers |
| Backend | FastAPI, container system Python, no virtual environment |
| Frontend | Next.js, connected through the API service |
| Database | PostgreSQL 16 for courses, files, chunks, graphs, and QA records |
| Vector Store | Qdrant 1.17.1, collection `knowledge_chunks` |
| Cache And Tasks | Redis 7 |
| Model API | OpenAI-compatible endpoint for embeddings, chat, and graph extraction |
| Retrieval | Child recall, fusion, rerank, then parent context assembly |
| Graph | Concepts, aliases, relations, and evidence chunk links |
| Quality Gate | No fallback, no zero vectors, DB/vector count consistency |

## Technology Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Frontend | Next.js, React, TypeScript | Course management, ingestion UI, search, QA, graph browsing, runtime settings |
| API | FastAPI, Pydantic, SQLAlchemy | REST APIs, validation, transactions, ingestion tasks, agent orchestration |
| Database | PostgreSQL 16 | Courses, file versions, chunks, graphs, QA sessions, execution traces |
| Vector Search | Qdrant 1.17.1 | Parent/child vector storage, dense recall, vector health checks |
| Lexical Search | PostgreSQL text data + `rank_bm25` | Child BM25 recall and lexical matching |
| Cache And Tasks | Redis 7 | Runtime cache, task coordination, service dependency |
| Parsing | PyMuPDF, PPTX/DOCX parsers, Markdown/HTML/Notebook parsers | Convert heterogeneous course files into structured sections and text |
| Model API | OpenAI-compatible embedding/chat API | Embeddings, summaries, keywords, concept extraction, relation extraction, answer generation |
| Reranking | Lightweight reranker, optional Cross-Encoder | Reorder fused retrieval candidates by relevance |
| Deployment | Docker Compose | Fixed service boundaries, dependency versions, local persistence |
| Testing | pytest, container system Python | Backend tests, pipeline tests, no-fallback quality gates |

## System Architecture

```mermaid
flowchart TB
    USER["User Browser"] --> WEB["Web App<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["API Service<br/>course-kg-api"]

    subgraph APP["Application Layer"]
        API --> ROUTES["API Routes<br/>courses / files / ingestion / search / QA / graph / settings"]
        ROUTES --> INGEST["Ingestion Pipeline<br/>parse -> chunk -> knowledge augmentation -> vector upsert -> graph extraction"]
        ROUTES --> SEARCH["Retrieval Pipeline<br/>child recall -> fusion -> rerank -> parent context"]
        ROUTES --> AGENT["Agent QA<br/>analyze -> route -> retrieve -> grade evidence -> answer -> trace"]
        ROUTES --> GRAPH["Knowledge Graph<br/>concepts / relations / evidence / node details"]
    end

    subgraph INFRA["Infrastructure"]
        POSTGRES[("PostgreSQL 16<br/>metadata, chunks, graph, QA records")]
        QDRANT[("Qdrant 1.17.1<br/>vector collection knowledge_chunks")]
        REDIS[("Redis 7<br/>tasks and runtime cache")]
    end

    subgraph STORAGE["Host Persistence"]
        DATA["data/<br/>course files, parser artifacts, database data"]
        MODEL_CACHE["data/models/huggingface<br/>reranker model cache"]
    end

    subgraph MODEL["External Model API"]
        LLM["OpenAI-compatible endpoint<br/>embeddings / chat / graph extraction"]
    end

    API --> POSTGRES
    API --> QDRANT
    API --> REDIS
    API -->|"OPENAI_BASE_URL"| LLM
    API --- DATA
    API --- MODEL_CACHE
    POSTGRES --- DATA
    REDIS --- DATA
```

## Core Capabilities

| Capability | Description |
| --- | --- |
| Multi-format parsing | Supports PDF, PPT/PPTX, DOCX, Markdown, TXT, Notebook, HTML, and image materials |
| Parent-child chunking | Parent chunks preserve full context; child chunks handle precise recall and reranking |
| Contextual embedding text | Embedding input includes metadata, parent summaries, neighbor summaries, keywords, table markers, and formula markers |
| Hybrid retrieval | Qdrant child dense recall fused with PostgreSQL child lexical recall |
| Reranking | Lightweight reranking by default, with optional Cross-Encoder reranking |
| Graph enhancement | Concepts and relations are linked through evidence chunks for relation, comparison, and multi-hop questions |
| Observable QA | Agent traces, retrieval audits, model call audits, and citations are recorded |
| Runtime checks | Health, runtime configuration, fallback status, and reranker status are exposed |

## Core Algorithms

### Hierarchical Chunking

DialoGraph uses a parent-child chunk structure to handle the context span problem in course materials:

1. Parsers convert source files into `ParsedSection` objects while preserving chapter, page, source type, table, and formula metadata.
2. Each structured section creates one parent chunk containing the complete section or natural paragraph.
3. Each parent chunk is split into child chunks for precise recall and reranking.
4. Markdown and Notebook files follow heading hierarchy; ordinary long text follows semantic boundaries, sentence boundaries, and safe length limits.
5. When `SEMANTIC_CHUNKING_ENABLED=true` and text length reaches `SEMANTIC_CHUNKING_MIN_LENGTH`, the system can use embedding-similarity semantic splitting.

This design avoids the weak recall of overly large chunks and the context loss of overly small chunks.

### Context-Enriched Embeddings

Child vectors are not built from child text alone. `contextual_embedding_text()` builds context-enriched embedding input:

```text
file metadata
chapter and source type
child content
parent summary or parent content
neighboring child summaries
keywords
table and formula markers
```

Parent chunks keep their own content, summary, and keywords. Child chunks inherit parent summary and include neighboring child summaries, reducing context loss in fine-grained chunks. The current embedding text version is `contextual_enriched_v2`.

### Small-To-Big Retrieval

The main retrieval path follows small-to-big retrieval:

```mermaid
flowchart LR
    Q["Query"] --> DENSE["Child Dense Recall"]
    Q --> BM25["Child BM25 Recall"]
    DENSE --> FUSION["Weighted Fusion"]
    BM25 --> FUSION
    FUSION --> RERANK["Rerank"]
    RERANK --> PARENT["Load Parent By parent_chunk_id"]
    PARENT --> RESULT["Child Evidence + Parent Context"]
```

Algorithm details:

- Qdrant and BM25 recall child chunks by default, avoiding parent/child competition in the same candidate pool.
- Dense and lexical results are combined by weighted score fusion; dense-only hits remain valid when lexical recall has no match.
- Reranking operates on fused child candidates.
- Final results attach `parent_content` through `parent_chunk_id`, giving the answer model complete local context.
- Result metadata includes `retrieval_granularity=child_with_parent_context`, rerank scores, and model-call audit fields.

### Graph-Enhanced Retrieval

The graph is used to expand evidence candidates, not to replace textual evidence:

1. Run hybrid retrieval to get text candidates.
2. Use hit chunks and `evidence_chunk_id` to find related concepts and relations.
3. Expand one-hop relations and collect relation evidence chunks.
4. Merge relation evidence back into candidates with `graph_boost`.
5. Rerank and attach parent context through the same final path.

This keeps graph expansion grounded in text evidence and avoids generating answers from unsupported graph links.

### Agent QA

Agent QA decomposes retrieval-augmented answering into observable steps:

```text
question analysis -> routing -> query rewriting -> retrieval -> evidence grading -> context synthesis -> answer generation -> citation check -> self-check
```

Each node writes execution traces, so the frontend can show the current node, retrieved evidence, model audit, and final citations.

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
    Web->>API: Create ingestion task
    API->>Files: Store original file
    API->>DB: Create document, version, batch, and job records
    API->>API: Parse files and create parent/child chunks
    API->>Model: Generate summaries, keywords, and embeddings
    API->>Vector: Upsert parent and child vectors
    API->>DB: Activate chunks and document version
    API->>Model: Extract concepts and relations
    API->>DB: Store graph and evidence links
    API-->>Web: Stream ingestion logs and final status
```

Ingestion uses explicit transactions and file-level locks. Each course has at most one non-terminal ingestion batch at a time. Qdrant write failures are recorded for compensation, and startup checks interrupted batches.

## Data Model

```mermaid
erDiagram
    Course ||--o{ Document : has
    Course ||--o{ Concept : has
    Course ||--o{ IngestionBatch : has
    Course ||--o{ QASession : has
    Document ||--o{ DocumentVersion : versions
    DocumentVersion ||--o{ Chunk : chunks
    Chunk ||--o{ Chunk : children
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source
    Concept ||--o{ ConceptRelation : target
    IngestionBatch ||--o{ IngestionJob : jobs
    IngestionJob ||--o{ IngestionCompensationLog : compensations
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

| Table | Purpose |
| --- | --- |
| `courses` | Course workspace |
| `documents` / `document_versions` | File metadata and file versions |
| `chunks` | Parent chunks, child chunks, summaries, keywords, vector status, and evidence text |
| `concepts` / `concept_aliases` / `concept_relations` | Concepts, aliases, relations, and evidence chunk links |
| `ingestion_batches` / `ingestion_jobs` | Batch ingestion and per-file jobs |
| `ingestion_logs` / `ingestion_compensation_logs` | SSE logs and compensation records |
| `qa_sessions` / `agent_runs` / `agent_trace_events` | QA sessions, agent runs, and node traces |

## Chunking And Embeddings

| Stage | Behavior |
| --- | --- |
| Structural parsing | Extracts chapters, pages, tables, formulas, cells, and image text by file type |
| Parent chunk creation | Preserves complete sections or natural paragraphs for answer context |
| Child chunk creation | Splits parent chunks by semantic boundaries, sentence boundaries, and safe length limits |
| Knowledge augmentation | Generates summaries, keywords, and content-kind markers |
| Embedding input | Built by `contextual_embedding_text()` as context-enriched text |
| Version marker | Current embedding text version is `contextual_enriched_v2` |

Retrieval only sends child chunks through recall and reranking, then attaches parent metadata:

```text
parent_chunk_id
parent_content
retrieval_granularity=child_with_parent_context
```

## Retrieval And QA

```mermaid
flowchart LR
    Q["User Question"] --> ANALYZE["Question Analysis"]
    ANALYZE --> DENSE["Child Dense Recall"]
    ANALYZE --> LEXICAL["Child Lexical Recall"]
    DENSE --> FUSION["Weighted Fusion"]
    LEXICAL --> FUSION
    FUSION --> RERANK["Rerank"]
    RERANK --> CONTEXT["Attach Parent Context"]
    CONTEXT --> ANSWER["Generate Answer And Citations"]
    ANALYZE -. "relation, comparison, multi-hop" .-> GRAPH["Graph-Enhanced Retrieval"]
    GRAPH --> RERANK
```

| Path | Use Case | Output |
| --- | --- | --- |
| Hybrid retrieval | Definitions, formulas, examples, factual questions | Child evidence, parent context, rerank scores |
| Graph-enhanced retrieval | Relation, comparison, and multi-hop questions | Text evidence, related concepts, relation evidence |
| Agent QA | Questions that need analysis, rewriting, evidence grading, and tracing | Answer, citations, model audit, node trace |

## Technical Advantages

| Advantage | Practical Effect |
| --- | --- |
| Balanced context and precision | Child chunks provide precise recall; parent chunks provide complete context, reducing both vague large-chunk retrieval and small-chunk context loss |
| Evidence-first answering | Answers are grounded in real chunks and parent context; graph relations must still link back to evidence chunks |
| Course-material awareness | Chapters, pages, tables, formulas, Notebook cells, and source types are preserved for better academic retrieval |
| Auditability | Results carry scores, parent context, reranking data, model-call audit fields, fallback state, and citations |
| Maintainability | Docker fixes service boundaries; PostgreSQL, Qdrant, and Redis have clear responsibilities; scripts support re-embedding and quality checks |
| Explicit quality gates | Fallback is disabled, zero vectors are checked, and DB/Qdrant count consistency is verified |
| Extensibility | Reranking, semantic chunking, graph enhancement, and model providers are isolated through configuration or service layers |

## Configuration

Copy the configuration template:

```powershell
Copy-Item .env.example .env
```

Common settings:

| Variable | Description |
| --- | --- |
| `API_HOST_PORT` / `WEB_HOST_PORT` | Host access ports |
| `DATABASE_URL` | PostgreSQL connection string |
| `QDRANT_URL` / `QDRANT_COLLECTION` | Qdrant URL and collection |
| `REDIS_URL` | Redis URL |
| `COURSE_NAME` | Default course name |
| `DATA_ROOT` | Local data root |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | OpenAI-compatible model endpoint |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | Embedding model and dimensions |
| `CHAT_MODEL` | Chat model |
| `ENABLE_MODEL_FALLBACK` | Model fallback switch, default `false` |
| `ENABLE_DATABASE_FALLBACK` | Database fallback switch, default `false` |
| `RERANKER_ENABLED` / `RERANKER_MODEL` | Cross-Encoder reranker settings |
| `SEMANTIC_CHUNKING_ENABLED` | Semantic chunking switch |
| `SEMANTIC_CHUNKING_MIN_LENGTH` | Minimum text length for semantic chunking |

Docker Compose overrides infrastructure URLs inside the API container:

```text
DATABASE_URL=postgresql+psycopg://postgres:postgres@postgres:5432/course_kg
QDRANT_URL=http://qdrant:6333
REDIS_URL=redis://redis:6379/0
```

If the host can reach the model provider but container networking is unstable, enable the model bridge:

```env
MODEL_BRIDGE_ENABLED=true
MODEL_BRIDGE_PORT=8765
```

The bridge forwards to the real model API. It does not replace the model and is not a fallback path.

## Running

```powershell
# Build images
docker compose -f infra/docker-compose.yml build api web

# Start the full stack
docker compose -f infra/docker-compose.yml up -d postgres redis qdrant api web

# Check containers
docker ps

# Check health
curl http://127.0.0.1:8000/api/health
curl http://127.0.0.1:8000/api/settings/runtime-check
```

Web app:

```text
http://127.0.0.1:3000
```

## Tests And Quality Gates

Tests run inside the API container system Python:

```powershell
docker exec course-kg-api python -m pytest apps/api/tests
```

Common quality checks:

```powershell
docker exec course-kg-api python scripts/quality_gate.py
docker exec course-kg-api python scripts/analyze_chunk_quality.py
```

Acceptance criteria:

| Check | Expected |
| --- | --- |
| Health | `/api/health` returns `degraded_mode=false` |
| Runtime configuration | `/api/settings/runtime-check` has no blocking issues |
| Model fallback | `ENABLE_MODEL_FALLBACK=false` |
| Database fallback | `ENABLE_DATABASE_FALLBACK=false` |
| Embedding call | Retrieval audit shows the real embedding API was called |
| Fallback reason | `fallback_reason` is empty |
| Vector health | Qdrant has no zero vectors |
| Count consistency | Active chunks match valid vectors |

## Version Control Rules

Not tracked:

- `.env` and local secrets.
- `data/`, database files, model caches, and runtime outputs.
- `node_modules/`, `.next/`, cache directories, and test reports.
- `comparative_experiment/`.

Tracked:

- Source code, tests, scripts, Docker orchestration, configuration templates, and README files.
