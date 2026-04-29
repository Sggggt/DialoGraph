**English** | [中文](./README.md)

# DialoGraph

DialoGraph is a local course knowledge-base system. It parses PDF, PPT/PPTX, DOCX, Markdown, TXT, Notebook, HTML and image materials into searchable text chunks, vector indexes, concept cards, knowledge-graph relations and citation-backed QA sessions.

The system supports multi-course isolation: each course has its own file directory, ingestion batches, graph, search results and QA history.

## Architecture

```mermaid
flowchart TB
    U["User Browser"] --> W["Next.js Web<br/>apps/web"]
    W -->|HTTP / SSE + API Key| SECURITY["Auth & CORS Middleware<br/>API Key / origin allowlist"]
    SECURITY --> API["FastAPI API<br/>apps/api"]

    subgraph API_RUNTIME["API Runtime"]
        API --> ROUTES["API Routes<br/>courses / files / search / qa / graph"]
        ROUTES --> INGEST["Ingestion Service<br/>parse -> inactive DB version -> vector upsert -> activate"]
        ROUTES --> RETRIEVAL["Retrieval Service<br/>dense + lexical + RRF + 1-hop GraphRAG"]
        ROUTES --> AGENT["LangGraph Agent<br/>rewrite / route / grade / answer / live trace"]
        INGEST --> PARSERS["Document Parsers<br/>PDF / PPTX / DOCX / Markdown / Notebook / OCR"]
        INGEST --> GRAPH["Concept Graph Builder<br/>concepts + relations"]
        INGEST --> COMP["Compensation Recovery<br/>IngestionCompensationLog"]
        AGENT --> TRACE["SSE trace publisher<br/>agent_trace_events"]
    end

    subgraph WORKERS["Optional Background Components"]
        CELERY["Celery Worker<br/>apps/worker"] --> INGEST
        WATCHER["Watchdog File Watcher"] --> CELERY
    end

    subgraph STORES["Storage Layer"]
        DB[("PostgreSQL<br/>primary metadata store")]
        SQLITE[("SQLite fallback<br/>explicit compatibility path only")]
        QDRANT[("Qdrant<br/>default vector store: knowledge_chunks")]
        FALLBACK[("Fallback JSON vector index<br/>fallback_compat tests only")]
        FS["Local data root<br/>data/<Course Name>/"]
        REDIS[("Redis<br/>Celery broker")]
    end

    API --> DB
    API -. "ENABLE_DATABASE_FALLBACK=true" .-> SQLITE
    API --> QDRANT
    COMP --> QDRANT
    API -. "ENABLE_MODEL_FALLBACK=true" .-> FALLBACK
    API --> FS
    CELERY --> REDIS

    subgraph MODEL["Model Provider"]
        LLM["OpenAI-compatible endpoint<br/>DashScope / OpenAI / compatible APIs"]
    end

    API -->|embeddings / chat / graph extraction| LLM
```

## Components

- `apps/web`: Next.js frontend workspace with upload, search, QA, graph, concept cards and settings pages.
- `apps/api`: FastAPI backend handling course management, file ingestion, parsing, chunking, embedding, retrieval, knowledge-graph building and Agent QA.
- `apps/worker`: Optional background components including a Celery ingestion worker and a directory watcher.
- `packages/shared`: Shared TypeScript data contracts between frontend and backend.
- `infra`: Local infrastructure with Docker Compose for PostgreSQL, Redis and Qdrant.

## Data Model

The system uses SQLAlchemy ORM to manage the following core tables:

```mermaid
erDiagram
    Course ||--o{ Document : has
    Course ||--o{ Concept : has
    Course ||--o{ IngestionBatch : has
    Document ||--o{ DocumentVersion : versions
    Document ||--o{ Chunk : chunks
    DocumentVersion ||--o{ Chunk : chunks
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source_relations
    IngestionBatch ||--o{ IngestionJob : jobs
    IngestionBatch ||--o{ IngestionLog : logs
    IngestionJob ||--o{ IngestionCompensationLog : compensations
    Course ||--o{ IngestionCompensationLog : vector_compensations
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

Key constraints:

- `Course.name` UNIQUE — course names are unique
- `Concept(course_id, normalized_name)` UNIQUE — concept deduplication within a course
- `DocumentVersion(document_id, version)` UNIQUE — version numbers are unique per document
- All primary keys use `UUID v4` (`String(36)`), suitable for distributed setups
- `TimestampMixin` automatically maintains `created_at` / `updated_at`
- Schema evolution is handled at startup via `SCHEMA_PATCHES` (`ALTER TABLE ADD COLUMN`)

## Storage Coordination

The system involves three storage backends with different transactional guarantees:

| Storage | Technology | Transactional Guarantee |
|---------|-----------|------------------------|
| Relational DB | SQLAlchemy (PG/SQLite) | `autocommit=False`, full ACID |
| Vector Index | Qdrant / FallbackJSON | No distributed transaction |
| File System | Local disk | No transaction |

**Cross-storage consistency strategies**:

- **Graph building** uses a single-transaction pattern (DELETE → INSERT → single COMMIT), fully ACID-compliant.
- **Document ingestion** uses two-phase activation: commit inactive new versions and chunks first, upsert vectors, then activate the new version and deactivate the old one.
- **Retrieval layer** performs secondary validation against the DB for results returned by the vector store (filtering deleted or inactive chunks) to defend against cross-storage inconsistency.
- **Batch operations** use a `rollback() → get() → mark failed → commit()` exception-handling pattern so that a single file failure does not pollute the entire batch.
- **Process restart recovery**: `finalize_interrupted_batches()` runs during FastAPI lifespan startup to process pending vector compensation logs and mark incomplete batches as failed.

## Concurrency & Async Model

```
FastAPI (uvicorn) ─── async API routes
  ├── BackgroundTasks ─── async callable / Celery entrypoint
  │     └── run_uploaded_files_ingestion (event-loop reuse)
  ├── LLM calls ─── httpx.AsyncClient / asyncio.to_thread(curl)
  ├── Graph extraction ─── asyncio.gather() + Semaphore(2)
  └── QA streaming ─── SSE token + trace events
```

**Concurrency control mechanisms**:

| Mechanism | Location | Scope |
|-----------|----------|-------|
| SQLAlchemy Session | `autocommit=False` | DB transaction isolation |
| `source_path` application lock | `ingest_file` | Serializes ingestion of the same file |
| Non-terminal batch mutex | `create_sync_batch` / `create_uploaded_files_batch` | Allows one active ingestion batch per course |
| `asyncio.Semaphore(2)` | `extract_llm_graph_payloads` | LLM API concurrency throttling |
| `portalocker` / process lock | `FallbackVectorStore` | Multi-process fallback JSON vector file locking |
| LRU lock registry | `_VECTOR_FILE_LOCKS` / `_SOURCE_PATH_LOCKS` | Bounds lock-table growth during long-running processes |
| fsync file write | `_write` (temp+replace+fsync) | Fallback vector file write integrity |

**Design decision**: Each LangGraph node in the Agent flow calls `db.commit()` to update `current_node` and trace events. This is an intentional observability design allowing the frontend to track Agent execution progress in real time via `/tasks/{run_id}`.

## Fallback Policy

Fallback is locked by default:

```env
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

This means the system will not silently degrade to fake embeddings, extractive answers, local JSON vector indexes or SQLite. The default requires:

- `OPENAI_API_KEY` available for embedding, chat and graph extraction.
- `QDRANT_URL` pointing to an accessible Qdrant instance.
- `DATABASE_URL` pointing to an accessible PostgreSQL instance.

Only explicitly unlock for local offline debugging:

```env
ENABLE_MODEL_FALLBACK=true
ENABLE_DATABASE_FALLBACK=true
```

When unlocked, the system may use deterministic local hash embeddings, extractive fallback answers, or `data/<Course Name>/ingestion/vector_index.json` as a vector index fallback. These results are suitable for development verification only, not for production knowledge-base quality assessment.

The database-layer SQLite fallback must also be explicitly enabled. Do not rely on SQLite fallback in production, and do not use fallback compatibility tests as the default release gate.

## Data Layout

The default data root is `data/`. Each course creates an isolated directory:

```text
data/
  <Course Name>/
    storage/       uploaded files and archived copies
    ingestion/     extracted JSON and optional fallback vector_index.json
    source/        optional watched source files
  qdrant/          Qdrant persistent storage
  postgres/        PostgreSQL persistent storage
  redis/           Redis persistent storage
```

Main persistence locations:

- Graph nodes and relations: PostgreSQL tables `concepts`, `concept_relations`.
- QA history: PostgreSQL table `qa_sessions`, messages in the `transcript` field.
- Agent run traces: `agent_runs`, `agent_trace_events`.
- Documents, versions, chunks and ingestion batches: `documents`, `document_versions`, `chunks`, `ingestion_batches`, `ingestion_jobs`.
- Vector index: Qdrant collection `knowledge_chunks`.

## Prerequisites

- Node.js `>= 20.9.0`
- Python `>= 3.11`
- `uv` for Python dependency management
- Docker Desktop or Docker Engine with Compose v2

Install `uv` if needed:

```powershell
python -m pip install uv
```

## Configuration

Create the root environment file:

```powershell
Copy-Item .env.example .env
```

Minimum local development configuration:

```env
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/knowledge_base
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=knowledge_chunks
REDIS_URL=redis://localhost:6379/0
COURSE_NAME=Sample Course
DATA_ROOT=./data
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus
EMBEDDING_DIMENSIONS=1024
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
API_KEYS=
```

If you use DashScope or another OpenAI-compatible endpoint, set `OPENAI_BASE_URL` and `OPENAI_API_KEY` accordingly.

## Install Dependencies

Install frontend workspace dependencies from the repo root:

```powershell
npm install
```

Install API dependencies:

```powershell
cd apps/api
uv sync
```

Install worker dependencies if you need background ingestion:

```powershell
cd apps/worker
uv sync
```

## Build and Start Backend Infrastructure

The current repository has Docker Compose for backend infrastructure only: PostgreSQL, Redis and Qdrant. It does not currently include Dockerfiles for API/Web/Worker application images.

Pull the infrastructure images:

```powershell
docker compose -f infra/docker-compose.yml pull
```

Start the backend infrastructure:

```powershell
docker compose -f infra/docker-compose.yml up -d
```

Check status:

```powershell
docker compose -f infra/docker-compose.yml ps
```

Recreate containers after image updates:

```powershell
docker compose -f infra/docker-compose.yml pull
docker compose -f infra/docker-compose.yml up -d --force-recreate
```

`docker compose build` is not useful for the current infra file because all three services use public images directly (`postgres:16`, `redis:7`, `qdrant/qdrant:v1.13.2`) and no local `build:` context is defined.

## Start Application Services

Recommended Windows launcher from the repo root:

```powershell
.\start-app.ps1
```

The launcher starts:

- API on `http://127.0.0.1:8000`
- Web on `http://127.0.0.1:3000`
- Browser path defaults to `/graph`

Run without opening a browser:

```powershell
.\start-app.ps1 -NoBrowser
```

Use custom ports:

```powershell
.\start-app.ps1 -BackendPort 8001 -FrontendPort 3001 -OpenPath "/search"
```

Manual API start:

```powershell
cd apps/api
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Manual Web start:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000/api"
npm run dev --workspace web -- --hostname 127.0.0.1 --port 3000
```

Optional worker:

```powershell
cd apps/worker
uv run celery -A worker_app.celery_app worker --loglevel=info
```

Optional watched-folder ingestion:

```powershell
cd apps/worker
uv run python -m worker_app.watcher
```

## Build Frontend

Type-check and build the web app:

```powershell
npm run typecheck:web
npm run build:web
```

Start the production Next.js server after build:

```powershell
$env:NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8000/api"
npm run start --workspace web
```

## Ingestion Flow

```mermaid
sequenceDiagram
    participant User
    participant Web
    participant API
    participant FS as Course Storage
    participant DB as PostgreSQL
    participant Model as Model API
    participant Qdrant

    User->>Web: Upload or sync course files
    Web->>API: POST /api/files/upload or /api/ingestion/parse-uploaded-files
    API->>FS: Save archived copy
    API->>API: Parse document (PDF/PPTX/DOCX/MD/Notebook/OCR)
    API->>API: Chunk, deduplicate and filter (text 800/120, code 700/100)
    API->>Model: Create embeddings from metadata-enriched text (batch, async)
    API->>Qdrant: Upsert vectors
    API->>DB: Store documents, versions, chunks (commit)
    API->>Model: Extract concept graph payload (Semaphore-throttled)
    API->>DB: Rebuild graph (single-transaction DELETE→INSERT→COMMIT)
    API-->>Web: Batch status via SSE stream
```

## RAG Architecture

The diagram below shows the complete RAG (Retrieval-Augmented Generation) pipeline, covering both the offline indexing stage and the online query stage:

```mermaid
flowchart TB
    subgraph OFFLINE["Offline Indexing Stage - during ingestion"]
        direction TB
        SRC["Source Files<br/>PDF / PPTX / DOCX / MD / Notebook / HTML / Images"]
        SRC --> PARSE["Document Parsers<br/>Structured extraction + OCR"]
        PARSE --> CHUNK["Semantic Chunking + Dedup/Filter<br/>text=800/120<br/>code=700/100"]
        CHUNK --> EMB["Metadata-Enriched Embedding<br/>Document / Chapter / Section / Source Type / Content Kind<br/>DB chunk.content stays original"]
        CHUNK --> META["Metadata Extraction<br/>chapter / type / page / title"]
        EMB --> VEC_W[("Vector Store Write<br/>Qdrant / FallbackJSON")]
        META --> DB_W[("Relational DB Write<br/>Document, Chunk")]
        CHUNK --> LLM_G["LLM Graph Extraction<br/>Semaphore throttled"]
        LLM_G --> GRAPH_W[("Knowledge Graph Write<br/>Concept, Relation<br/>single-transaction ACID")]
    end

    subgraph ONLINE["Online Query Stage - at question time"]
        direction TB
        Q["User Question"] --> QA["Query Analysis<br/>tokenization / intent detection"]
        QA --> ROUTE{"Route Decision"}
        ROUTE -->|direct / clarify| DIRECT["Direct Answer<br/>no retrieval needed"]
        ROUTE -->|notes / exercises / general| REWRITE["Query Rewrite<br/>LLM context disambiguation"]
        ROUTE -->|multi-hop / compare / relations| MULTI_HOP["Multi-hop Rewrite<br/>LLM sub-query generation"]
        REWRITE --> DENSE["Dense Retrieval<br/>vector similarity"]
        REWRITE --> LEX["Lexical Retrieval<br/>BM25-style full-text match"]
        MULTI_HOP --> DENSE
        MULTI_HOP --> LEX
        DENSE --> RRF["RRF Fusion Ranking<br/>k=60"]
        LEX --> RRF
        RRF --> GRAPH_EXT["GraphRAG Extension<br/>(multi-hop only) 1-hop neighbor boost"]
        GRAPH_EXT --> GRADE["Document Grading<br/>term overlap + score threshold"]
        GRADE -->|insufficient docs, retries remaining| RETRY["Retry Planner<br/>expand query terms"]
        RETRY --> DENSE
        GRADE -->|relevant docs found| CTX["Context Synthesis<br/>top-K excerpt assembly"]
        CTX --> GEN["Answer Generation<br/>LLM + course context constraints"]
        GEN --> CITE["Citation Checker<br/>filter irrelevant citations"]
        CITE --> SELF["Self-Check<br/>state closure"]
    end

    subgraph STORAGE["Storage Layer"]
        VEC[("Qdrant<br/>Vector Index")]
        DB[("PostgreSQL<br/>Metadata + Chunks")]
        KG[("Knowledge Graph<br/>Concepts + Relations")]
    end

    VEC_W -.-> VEC
    DB_W -.-> DB
    GRAPH_W -.-> KG
    DENSE --> VEC
    LEX --> DB
    GRAPH_EXT --> KG
    GRADE -->|secondary validation| DB
```

**Key design points**:

| Stage | Mechanism | Details |
|-------|-----------|---------|
| Chunking | Evaluated best strategy `chunk_800_metadata_enriched_v1` | Text uses `chunk_size=800, overlap=120`; code uses `chunk_size=700, overlap=100`; recursive separators and Markdown heading hierarchy are still used |
| Dedup / filtering | Document-level and chunk-level cleanup | Documents deduplicate by normalized title + checksum; chunks deduplicate by normalized content hash; filters TOC pages, page/figure numbers, mojibake, short low-information blocks, notebook output and low-information pure code |
| Embedding text | Metadata-enriched while preserving stored content | Embedding input prepends Document / Chapter / Section / Source Type / Content Kind; `chunks.content` remains the original chunk text; Qdrant payload marks `embedding_text_version=metadata_enriched_v1` |
| Embedding | Async batched + 3-tier fallback | OpenAI-compatible API → retry (429/5xx) → deterministic hash fallback |
| Retrieval | Dense + lexical + RRF fusion | Two independent recall paths, RRF (k=60) fusion ranking to avoid single-path misses |
| Grading | Term overlap + content-type weighting | Text +1.1 / code -1.8 / title match +1.4 |
| Consistency | Retrieval secondary validation | Vector results cross-validated against DB, filtering deleted and inactive chunks |
| Graph | Single-transaction full rebuild | DELETE → INSERT → COMMIT, fully ACID-compliant |

### GraphRAG Mechanism

When a query is routed to the "multi-hop / relations" branch, the system activates Graph-Enhanced Retrieval (GraphRAG). The following diagram illustrates its 1-hop expansion workflow:

```mermaid
graph LR
    BASE[Hybrid Search Base Chunks] --> REL[Extract 1-Hop Neighbor Relations]
    REL --> NEW[Discover New Evidence Chunks]
    NEW --> BOOST[Calculate Graph Boost Score]
    BOOST --> MERGE[Merge & Re-rank with Base]
    BASE --> MERGE
```

## Agent QA Flow

```mermaid
graph LR
    A[query_analyzer] --> B[router]
    B -->|retrieve| C[query_rewriter]
    B -->|multi-hop| C2[multi_hop_rewriter]
    B -->|direct/clarify| G[answer_generator]
    C --> D[hybrid_retriever<br/>Dense+Lexical+RRF]
    C2 --> D2[graph_enhanced_retriever<br/>+ GraphRAG]
    D --> E[document_grader]
    D2 --> E
    E -->|has docs| F[context_synthesizer]
    E -->|retry < 2| R[retry_planner]
    R --> D
    F --> G
    G --> H[citation_checker]
    H --> I[self_check]
```

Each node updates `AgentRun.current_node` and `AgentTraceEvent` in real time (commit per node) to support frontend progress tracking.

## Main API Endpoints

- `GET /api/courses`
- `POST /api/courses`
- `GET /api/courses/current/dashboard?course_id=...`
- `GET /api/courses/current/graph?course_id=...`
- `GET /api/graph/chapters/{chapter}?course_id=...`
- `GET /api/graph/nodes/{concept_id}?course_id=...`
- `GET /api/concepts?course_id=...`
- `POST /api/files/upload?course_id=...`
- `POST /api/ingestion/parse-uploaded-files`
- `POST /api/ingestion/parse-storage?course_id=...`
- `GET /api/ingestion/batches/{batch_id}`
- `GET /api/ingestion/batches/{batch_id}/logs` (SSE)
- `POST /api/search`
- `POST /api/qa`
- `POST /api/qa/stream` (SSE)
- `POST /api/agent`
- `GET /api/tasks/{run_id}`
- `GET /api/sessions?course_id=...`
- `GET /api/sessions/{session_id}/messages`
- `DELETE /api/sessions/{session_id}`
- `GET /api/settings/model`
- `PUT /api/settings/model`

## Development Notes

- Keep `.env`, `data/`, local databases and generated logs out of Git.
- `ingestion/` contains derived extraction artifacts; it can be regenerated from stored source documents.
- `storage/` contains uploaded or copied source files; deleting it removes the material needed for re-ingestion.
- The API uses lightweight schema patching at startup (`SCHEMA_PATCHES` + `ALTER TABLE ADD COLUMN`) instead of Alembic migrations.
- Authentication and production-grade authorization are not implemented yet.
- The `FallbackVectorStore` uses thread-level locks with atomic temp-file writes; it is safe for single-process deployments but not for multi-worker configurations.
- `finalize_interrupted_batches()` runs at startup to mark incomplete batches as failed, providing crash recovery for the ingestion pipeline.
