**English** | [中文](./README.md)

# DialoGraph

DialoGraph is a Docker-first local course knowledge-base system. It parses PDF, PPT/PPTX, DOCX, Markdown, TXT, Notebook, HTML and image materials into searchable chunks, vector indexes, concept graphs and citation-backed QA.

The default stack uses real PostgreSQL, Qdrant, Redis and an OpenAI-compatible model API. Model fallback and database fallback are disabled by default.

## System Architecture

```mermaid
flowchart TB
    U["User Browser"] --> WEB["Next.js Web<br/>course-kg-web"]
    WEB -->|"HTTP / SSE"| API["FastAPI API<br/>course-kg-api"]

    subgraph APP["Project Application Images"]
        WEB
        API --> ROUTES["API Routes<br/>courses / files / ingestion / search / qa / graph / settings"]
        ROUTES --> INGEST["Ingestion Pipeline<br/>parse -> chunk -> embed -> vector upsert -> graph extraction"]
        ROUTES --> RETRIEVAL["Retrieval Pipeline<br/>Dense recall + BM25 recall + WSF fusion + rerank"]
        ROUTES --> AGENT["Agent QA<br/>rewrite / route / retrieve / grade / answer / trace"]
        ROUTES --> GRAPH["Knowledge Graph<br/>concepts / relations / chapters / node detail"]
    end

    subgraph INFRA["Reusable Infrastructure"]
        POSTGRES[("postgres:16<br/>metadata, chunks, graph, QA sessions")]
        REDIS[("redis:7<br/>broker / runtime cache")]
        QDRANT[("qdrant/qdrant:v1.13.2<br/>knowledge_chunks vectors")]
        RERANK_CPU["text-reranker-runtime:cpu<br/>CrossEncoder rerank"]
        RERANK_CUDA["text-reranker-runtime:cuda<br/>CrossEncoder rerank + GPU"]
    end

    subgraph HOST["Host-Mounted Directories"]
        DATA["data/<br/>course files, ingestion artifacts, DB data"]
        MODELS["models/<br/>Hugging Face cache"]
    end

    subgraph MODEL["External Model API"]
        LLM["OpenAI-compatible endpoint<br/>embeddings / chat / graph extraction"]
    end

    API --> POSTGRES
    API --> REDIS
    API --> QDRANT
    API -->|"HTTP rerank via RERANKER_URL"| RERANK_CPU
    API -. "RERANKER_DEVICE=cuda" .-> RERANK_CUDA
    API -->|"real API, fallback disabled"| LLM

    POSTGRES --- DATA
    REDIS --- DATA
    QDRANT --- DATA
    API --- DATA
    RERANK_CPU --- MODELS
    RERANK_CUDA --- MODELS
```

The API image stays lightweight and does not include PyTorch, CUDA or `sentence-transformers`. Reranking runs in the reusable `text-reranker-runtime:*` container when `RERANKER_ENABLED=true`; CPU/CUDA selection is controlled by `RERANKER_DEVICE`.

## Repository Layout

```text
apps/api/             FastAPI backend
apps/web/             Next.js frontend
apps/worker/          Optional background worker
packages/shared/      Shared TypeScript contracts
infra/                Docker Compose and reusable reranker runtime
data/                 Local persistent data
models/               Local model cache
```

Main runtime persistence:

```text
data/postgres         PostgreSQL data
data/qdrant           Qdrant data
data/redis            Redis data
data/storage          Uploaded and archived files
data/ingestion        Derived parsing artifacts
models/huggingface    Reranker model cache
```

## Data Model Architecture

```mermaid
erDiagram
    Course ||--o{ Document : has
    Course ||--o{ Concept : has
    Course ||--o{ IngestionBatch : has
    Course ||--o{ IngestionJob : has
    Course ||--o{ QASession : has
    Course ||--o{ AgentRun : has
    Document ||--o{ DocumentVersion : versions
    Document ||--o{ Chunk : chunks
    DocumentVersion ||--o{ Chunk : chunks
    Concept ||--o{ ConceptAlias : aliases
    Concept ||--o{ ConceptRelation : source
    Concept ||--o{ ConceptRelation : target
    IngestionBatch ||--o{ IngestionJob : jobs
    IngestionBatch ||--o{ IngestionLog : logs
    IngestionJob ||--o{ IngestionCompensationLog : compensations
    QASession ||--o{ AgentRun : runs
    AgentRun ||--o{ AgentTraceEvent : traces
```

Core tables:

- `courses`: course workspace, with unique course names.
- `documents` / `document_versions`: document metadata and versions, supporting inactive-to-active version activation.
- `chunks`: searchable text chunks with original content, snippet, chapter, page, source type and embedding status.
- `concepts` / `concept_aliases` / `concept_relations`: graph concepts, aliases and relations; relations can point to `evidence_chunk_id`.
- `ingestion_batches` / `ingestion_jobs` / `ingestion_logs` / `ingestion_compensation_logs`: batch ingestion, per-file jobs, SSE logs and vector compensation records.
- `qa_sessions` / `agent_runs` / `agent_trace_events`: QA history, agent runs and node-level traces.

## Concurrency And Async Model

```mermaid
flowchart LR
    REQ["FastAPI request"] --> ASYNC["async route handlers"]
    ASYNC --> BG["BackgroundTasks<br/>batch ingestion"]
    ASYNC --> LLM["async model calls<br/>httpx / thread offload"]
    BG --> LOCKS["source_path locks<br/>batch mutex"]
    BG --> DBTX["SQLAlchemy transaction"]
    BG --> VECTOR["Qdrant upsert"]
    BG --> LOGS["SSE ingestion logs"]
    AGENT["Agent graph"] --> NODE["node execution"]
    NODE --> TRACE["commit current_node + trace event"]
```

Concurrency controls:

- SQLAlchemy sessions use explicit transactions; failures roll back the affected file or batch segment.
- Each course keeps at most one non-terminal ingestion batch at a time.
- File ingestion is serialized by an application-level `source_path` lock.
- Graph extraction uses bounded concurrency to avoid overloading the model API.
- Each Agent node commits `current_node` and `agent_trace_events`, so the frontend can show live progress.
- Qdrant failures are recorded in compensation logs, and startup recovery finalizes interrupted batches.

## Fallback Policy

Default configuration:

```env
ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

Default behavior:

- Model API failures are surfaced directly; the system does not silently switch to fake embeddings or extractive answers.
- PostgreSQL failures are surfaced directly; the system does not silently switch to SQLite.
- Qdrant failures break retrieval instead of using local JSON fallback as a production path.
- `/api/health` returns `degraded_mode`; evaluation and normal operation should require it to be `false`.

Fallback paths are only for explicit offline development or compatibility tests. They should not be used for system-quality evaluation or production data decisions.

## Ingestion Flow

```mermaid
sequenceDiagram
    participant User
    participant Web
    participant API
    participant FS as data/storage
    participant DB as PostgreSQL
    participant Model as Model API
    participant Qdrant

    User->>Web: Upload or select course files
    Web->>API: POST /api/files/upload
    API->>FS: Store archived copy
    API->>DB: Create document/job
    Web->>API: POST /api/ingestion/parse-uploaded-files
    API->>DB: Create ingestion batch
    API-->>Web: batch_id
    API->>API: Parse PDF/PPT/DOCX/MD/Notebook/HTML/Image
    API->>API: Chunk, deduplicate, filter low-information blocks
    API->>Model: Generate metadata-enriched embeddings
    API->>Qdrant: Upsert vectors
    API->>DB: Activate document version and chunks
    API->>Model: Extract concepts and relations
    API->>DB: Rebuild graph in one transaction
    API-->>Web: SSE batch logs and final state
```

Ingestion write strategy:

- Parser artifacts are written under `data/ingestion`.
- Uploaded and archived source files are written under `data/storage`.
- Original chunk text is stored in PostgreSQL; embedding input is enriched with document, chapter, section and source metadata.
- Vectors are written to Qdrant before activating the new document version.
- Graph relations are stored in PostgreSQL and point evidence to real chunks.

## RAG Architecture

```mermaid
flowchart TB
    subgraph OFFLINE["Offline Indexing Stage"]
        SRC["Course files"] --> PARSE["Document parsers"]
        PARSE --> CHUNK["Semantic chunking<br/>dedup + filtering"]
        CHUNK --> EMBTXT["Metadata-enriched embedding text"]
        EMBTXT --> EMB["Embedding API"]
        EMB --> VEC[("Qdrant knowledge_chunks")]
        CHUNK --> DBW[("PostgreSQL chunks")]
        CHUNK --> GRAPH_EXTRACT["LLM graph extraction"]
        GRAPH_EXTRACT --> KG[("Concepts + Relations")]
    end

    subgraph ONLINE["Online Query Stage"]
        Q["User query"] --> QT["Query type classifier"]
        QT --> DENSE["Dense vector recall"]
        QT --> BM25["BM25 lexical recall"]
        DENSE --> WSF["Weighted Score Fusion<br/>query-type alpha + min-max"]
        BM25 --> WSF
        WSF --> RERANK["Optional external bge-reranker<br/>HTTP runtime"]
        RERANK --> TOPK["Top-K chunks"]
        TOPK --> ANSWER["Chat model answer<br/>with citations"]
    end

    VEC --> DENSE
    DBW --> BM25
    KG -. "pending controlled GraphRAG" .-> ONLINE
```

The primary retrieval path is:

```text
Dense vector recall + BM25 lexical recall + WSF fusion + optional external bge-reranker rerank
```

## GraphRAG Workflow

Graph construction, graph browsing and relation storage are available. GraphRAG-enhanced retrieval remains a pending upgrade and should join the primary ranking path only after semantic gating and evidence-support verification.

```mermaid
flowchart LR
    BASE["Hybrid retrieval top chunks"] --> SEED["Find concepts and relations<br/>from evidence chunks"]
    SEED --> HOP["1-hop relation expansion"]
    HOP --> EVIDENCE["Candidate evidence chunks"]
    EVIDENCE --> GATE["Semantic gate<br/>query-relation-evidence support"]
    GATE --> MERGE["Controlled merge"]
    MERGE --> RERANK["Rerank with base candidates"]
    RERANK --> FINAL["Final top-K context"]
```

Upgrade principles:

- Graph expansion may add candidate evidence, but it must not bypass textual evidence.
- Relations must be supported by evidence chunks, and relation types require semantic validation.
- Comparison and relationship questions are the first target for graph gating; definition questions use the primary retrieval path by default.

## Agent QA Flow

```mermaid
flowchart TB
    Q["User question"] --> ANALYZE["query_analyzer"]
    ANALYZE --> ROUTER["router"]
    ROUTER -->|"direct / clarify"| ANSWER["answer_generator"]
    ROUTER -->|"retrieve"| REWRITE["query_rewriter"]
    ROUTER -->|"multi-hop / relation"| MULTIHOP["multi_hop_rewriter"]
    REWRITE --> RETRIEVE["hybrid_retriever<br/>Dense + BM25 + WSF + optional rerank"]
    MULTIHOP --> RETRIEVE
    RETRIEVE --> GRADE["document_grader"]
    GRADE -->|"insufficient, retry available"| RETRY["retry_planner"]
    RETRY --> REWRITE
    GRADE -->|"has evidence"| CONTEXT["context_synthesizer"]
    CONTEXT --> ANSWER
    ANSWER --> CITE["citation_checker"]
    CITE --> SELF["self_check"]
    SELF --> FINAL["final response + trace"]
```

Agent runs are stored in `agent_runs`, and node events are stored in `agent_trace_events`. `/api/tasks/{run_id}` and `/api/agent/runs/{run_id}` expose run status.

## Configuration

Create the environment file:

```powershell
Copy-Item .env.example .env
```

Important settings:

```env
API_IMAGE=course-kg-api:local
WEB_IMAGE=course-kg-web:local
RERANKER_CPU_IMAGE=text-reranker-runtime:cpu
RERANKER_CUDA_IMAGE=text-reranker-runtime:cuda

DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/knowledge_base
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0

OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus

RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_ENABLED=true
RERANKER_DEVICE=cpu

ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

When `RERANKER_ENABLED=false`, retrieval returns the WSF-fused ranking directly and the reranker runtime is not started. This is not fallback. `RERANKER_DEVICE` only supports `cpu` and `cuda`, and only matters when reranking is enabled.

The frontend Settings page can toggle reranking directly. Before enabling it, Web calls `/api/settings/runtime-check` to verify `.env` / `.env.example` key sync, the reranker runtime, model cache, PostgreSQL, Qdrant and Redis connectivity. If infrastructure is incomplete, the UI opens a structured error dialog with repair commands instead of silently saving a broken configuration.

## Validate Existing Infrastructure

```powershell
docker run --rm postgres:16 postgres --version
docker run --rm redis:7 redis-server --version
docker image inspect qdrant/qdrant:v1.13.2
docker run --rm text-reranker-runtime:cpu python -c "import torch; print(torch.__version__)"
docker run --rm --gpus all text-reranker-runtime:cuda python -c "import torch; print(torch.cuda.is_available())"
```

The CUDA check should print `True`. If it does not, verify the NVIDIA Driver and NVIDIA Container Toolkit first.

## Build Images

Build only missing or changed images:

```powershell
docker build -f apps/api/Dockerfile -t course-kg-api:local .
docker build -f apps/web/Dockerfile -t course-kg-web:local .
docker build -f infra/reranker/Dockerfile.cpu -t text-reranker-runtime:cpu infra/reranker
docker build -f infra/reranker/Dockerfile.cuda -t text-reranker-runtime:cuda infra/reranker
```

`text-reranker-runtime:*` is reusable infrastructure and can be shared across projects. If an equivalent image already exists, set its image name in `.env`.

## Model Cache

The reranker model is mounted from the host and is not baked into images:

```text
models/huggingface -> /models/huggingface
```

Prefetch the model:

```powershell
docker run --rm -v "${PWD}\models:/models" -e HF_HOME=/models/huggingface text-reranker-runtime:cpu python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3', max_length=512, device='cpu')"
```

When a Hugging Face mirror is needed, add:

```powershell
-e HF_ENDPOINT=https://hf-mirror.com
```

## Start

Start the full application:

```powershell
.\start-app.ps1
```

Common options:

```powershell
.\start-app.ps1 -NoBrowser
.\start-app.ps1 -BackendPort 8001 -FrontendPort 3001 -OpenPath "/search"
```

Stop services:

```powershell
docker compose -f infra/docker-compose.yml down
docker compose -f infra/docker-compose.yml -f infra/docker-compose.cuda.yml down
```

## API Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health and degraded status |
| GET | `/api/settings/model` | Read model settings |
| PUT | `/api/settings/model` | Update model settings |
| GET | `/api/settings/runtime-check` | Check `.env` sync, reranker runtime and infrastructure status |
| GET | `/api/courses` | List courses |
| POST | `/api/courses` | Create course |
| DELETE | `/api/courses/{course_id}` | Delete course and data |
| GET | `/api/courses/current/dashboard` | Current course dashboard |
| POST | `/api/courses/current/refresh` | Refresh current course state |
| GET | `/api/course-files` | List course files |
| DELETE | `/api/course-files` | Remove a course file |
| POST | `/api/maintenance/cleanup-stale-data` | Clean stale data |
| POST | `/api/maintenance/cleanup-stale-graph` | Clean stale graph records |
| GET | `/api/courses/current/graph` | Course graph |
| GET | `/api/graph/chapters/{chapter}` | Chapter graph |
| GET | `/api/graph/nodes/{concept_id}` | Concept node detail |
| GET | `/api/concepts` | Concept cards |
| POST | `/api/files/upload` | Upload file |
| POST | `/api/ingestion/parse-uploaded-files` | Parse uploaded files |
| POST | `/api/ingestion/parse-storage` | Parse course storage directory |
| GET | `/api/ingestion/batches/{batch_id}` | Ingestion batch status |
| GET | `/api/ingestion/batches/{batch_id}/logs` | Ingestion logs over SSE |
| GET | `/api/jobs/{job_id}` | Per-file job status |
| POST | `/api/search` | Hybrid search |
| POST | `/api/qa` | Agent QA |
| POST | `/api/qa/stream` | Agent QA over SSE |
| POST | `/api/agent` | Agent call |
| GET | `/api/agent/runs/{run_id}` | Agent run status |
| GET | `/api/tasks/{run_id}` | Agent run status alias |
| GET | `/api/sessions` | List sessions |
| GET | `/api/sessions/{session_id}` | Session summary |
| GET | `/api/sessions/{session_id}/messages` | Session messages |
| DELETE | `/api/sessions/{session_id}` | Delete session |

## Development Notes

- Model fallback and database fallback are disabled by default.
- Do not commit `.env`, `data/`, `models/` or `output/`.
- The API container only carries project backend dependencies; PyTorch/CUDA live only in the reusable reranker runtime.
- Production data should use PostgreSQL + Qdrant, not SQLite or JSON fallback.
- API startup applies lightweight schema patches and finalizes interrupted ingestion batches; Alembic migrations are not used.
- Course data is isolated by `course_id`; uploaded files, parser artifacts, chunks, graph records and QA sessions are course-scoped.
- `retrieval_architecture.md` is a local design note; this README is the main project architecture entry point.
