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
        ROUTES --> RETRIEVAL["Retrieval Pipeline<br/>Dense recall + BM25 recall<br/>WSF when both channels hit + rerank"]
        ROUTES --> AGENT["Agent QA<br/>analyze / route / rewrite / retrieve / grade / answer / trace"]
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
        BRIDGE["Optional Windows model bridge<br/>127.0.0.1:${MODEL_BRIDGE_PORT}<br/>curl.exe forwarding"]
    end

    subgraph MODEL["External Model API"]
        LLM["OpenAI-compatible endpoint<br/>embeddings / chat / graph extraction"]
    end

    API --> POSTGRES
    API --> REDIS
    API --> QDRANT
    API -->|"HTTP rerank via RERANKER_URL"| RERANK_CPU
    API -. "RERANKER_DEVICE=cuda" .-> RERANK_CUDA
    API -->|"MODEL_BRIDGE_ENABLED=false<br/>real API, fallback disabled"| LLM
    API -. "MODEL_BRIDGE_ENABLED=true<br/>http://host.docker.internal:${MODEL_BRIDGE_PORT}" .-> BRIDGE
    BRIDGE -. "Windows curl.exe<br/>forwards to OPENAI_BASE_URL" .-> LLM

    POSTGRES --- DATA
    REDIS --- DATA
    QDRANT --- DATA
    API --- DATA
    RERANK_CPU --- MODELS
    RERANK_CUDA --- MODELS
```

The API image stays lightweight and does not include PyTorch, CUDA or `sentence-transformers`. Reranking runs in the reusable `text-reranker-runtime:*` container when `RERANKER_ENABLED=true`; CPU/CUDA selection is controlled by `RERANKER_DEVICE`. Model API traffic has two mutually exclusive paths: by default the API container calls `OPENAI_BASE_URL` directly; on Windows Docker Desktop, when the host can reach the provider but Linux containers cannot complete the provider TLS handshake, `MODEL_BRIDGE_ENABLED=true` starts a host-side model bridge and points the API container to `http://host.docker.internal:${MODEL_BRIDGE_PORT}`. The bridge only forwards to the real OpenAI-compatible provider; it does not replace the model and is not fallback.

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
        DENSE --> WSF["Weighted Score Fusion<br/>when BM25 also hits"]
        BM25 --> WSF
        DENSE --> DENSE_ONLY["Dense-only candidates<br/>when lexical channel has no hit"]
        WSF --> RERANK["Optional external bge-reranker<br/>HTTP runtime"]
        DENSE_ONLY --> RERANK
        RERANK --> TOPK["Top-K chunks"]
        TOPK --> ANSWER["Chat model answer<br/>with citations"]
    end

    VEC --> DENSE
    DBW --> BM25
    KG -. "multi-hop graph expansion" .-> ONLINE
```

The primary retrieval path is:

```text
Dense vector recall + BM25 lexical recall; WSF runs when both channels hit; dense-only results skip WSF; the external bge-reranker runs when enabled.
```

## GraphRAG Workflow

Graph construction, graph browsing and relation storage are available. The current `multi_hop_research` route uses `graph_enhanced_search`: it first runs hybrid retrieval, finds seed relations from the hit chunks' `evidence_chunk_id`, expands 1-hop relations, adds related evidence chunks as candidates, and merges them back with `graph_boost`. Semantic gating and relation-evidence support verification are still planned upgrades.

```mermaid
flowchart LR
    BASE["Hybrid retrieval top chunks"] --> SEED["Find seed relations<br/>by evidence_chunk_id"]
    SEED --> HOP["1-hop relation expansion"]
    HOP --> EVIDENCE["Related evidence chunks"]
    EVIDENCE --> BOOST["graph_boost merge<br/>with base candidates"]
    BOOST --> FINAL["Final top-K context"]
    EVIDENCE -. "planned semantic gate<br/>query-relation-evidence support" .-> BOOST
```

Upgrade principles:

- Graph expansion may add candidate evidence, but it must not bypass textual evidence.
- Relations must be supported by evidence chunks; a later upgrade will validate relation type against query intent.
- Comparison and relationship questions enter the multi-hop retrieval path; definition questions use the primary retrieval path by default.

## Agent QA Flow

```mermaid
flowchart TB
    Q["User question"] --> ANALYZE["query_analyzer"]
    ANALYZE --> ROUTER["router"]
    ROUTER -->|"direct / clarify"| TEMPLATE["template answer<br/>no chat model call"]
    ROUTER -->|"retrieve"| REWRITE["query_rewriter"]
    ROUTER -->|"multi-hop / relation"| REWRITE
    REWRITE --> SPLIT["split_multi_hop_query<br/>only for multi_hop_research"]
    SPLIT --> RETRIEVE["retrievers<br/>hybrid_search_chunks or graph_enhanced_search"]
    RETRIEVE --> GRADE["document_grader"]
    GRADE -->|"insufficient, retry available"| RETRY["retry_planner"]
    RETRY --> REWRITE
    GRADE -->|"has evidence"| CONTEXT["context_synthesizer"]
    CONTEXT --> ANSWER["answer_generator<br/>ChatProvider answer"]
    TEMPLATE --> CITE["citation_checker"]
    ANSWER --> CITE["citation_checker"]
    CITE --> SELF["self_check"]
    SELF --> FINAL["final response + trace"]
```

Agent runs are stored in `agent_runs`, and node events are stored in `agent_trace_events`. `/api/tasks/{run_id}` and `/api/agent/runs/{run_id}` expose run status. QA responses include `answer_model_audit`: retrieval-backed answers record `provider`, `model` and `external_called=true`; the `direct_answer` / `clarify` template branches record `skipped_reason` to make clear that the chat model was not called.

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

DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/course_kg
QDRANT_URL=http://localhost:6333
REDIS_URL=redis://localhost:6379/0

OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_RESOLVE_IP=
OPENAI_API_KEY=
EMBEDDING_MODEL=text-embedding-v4
CHAT_MODEL=qwen-plus

MODEL_BRIDGE_ENABLED=false
MODEL_BRIDGE_PORT=8765

RERANKER_MODEL=BAAI/bge-reranker-v2-m3
RERANKER_ENABLED=true
RERANKER_DEVICE=cpu
RERANKER_CANDIDATE_LIMIT=12

ENABLE_MODEL_FALLBACK=false
ENABLE_DATABASE_FALLBACK=false
```

The default example above targets DashScope's OpenAI-compatible endpoint, so `OPENAI_BASE_URL`, `EMBEDDING_MODEL`, `CHAT_MODEL` and `EMBEDDING_DIMENSIONS` must be treated as one matched set. When using another OpenAI-compatible provider, change all of them together and keep the embedding output dimension aligned with `EMBEDDING_DIMENSIONS`. With an empty `OPENAI_API_KEY`, Docker services can start, but upload parsing, search and QA model calls will fail; a real key is required before full-chain validation. Do not enable `ENABLE_MODEL_FALLBACK` or `ENABLE_DATABASE_FALLBACK`.

When `RERANKER_ENABLED=false`, retrieval returns the WSF-fused ranking directly and the reranker runtime is not started. This is not fallback. `RERANKER_DEVICE` only supports `cpu` and `cuda`, and only matters when reranking is enabled. `RERANKER_CANDIDATE_LIMIT` caps how many WSF candidates are sent to the cross-encoder reranker; keep it lower on CPU and raise it when CUDA is available.

The frontend Settings page can toggle reranking directly. Before enabling it, Web calls `/api/settings/runtime-check` to verify `.env` / `.env.example` key sync, the reranker runtime, model cache, PostgreSQL, Qdrant and Redis connectivity. If infrastructure is incomplete, the UI opens a structured error dialog with repair commands instead of silently saving a broken configuration.

`MODEL_BRIDGE_ENABLED=true` is only for Windows Docker Desktop environments where the host can reach the OpenAI-compatible provider but Linux containers cannot complete the provider TLS handshake. The launcher starts `infra/model-bridge/model_bridge.py` on the host and points the API container at `http://host.docker.internal:${MODEL_BRIDGE_PORT}`. The bridge forwards the original request to `OPENAI_BASE_URL` with optional `OPENAI_RESOLVE_IP`; it does not replace the model or enable fallback.

## Validate Base Images

```powershell
docker run --rm postgres:16 postgres --version
docker run --rm redis:7 redis-server --version
docker image inspect qdrant/qdrant:v1.13.2
```

These commands only verify that Docker can fetch PostgreSQL, Redis and Qdrant base images. `text-reranker-runtime:*` is built locally by this project; validate it after the build step below.

## Build Images

Build only missing or changed images:

```powershell
docker build -f apps/api/Dockerfile -t course-kg-api:local .
docker build -f apps/web/Dockerfile -t course-kg-web:local .
docker build -f infra/reranker/Dockerfile.cpu -t text-reranker-runtime:cpu infra/reranker
docker build -f infra/reranker/Dockerfile.cuda -t text-reranker-runtime:cuda infra/reranker
```

On a new machine, build at least the API, Web and CPU reranker images. Build the CUDA reranker only when `RERANKER_DEVICE=cuda`. `text-reranker-runtime:*` is reusable infrastructure and can be shared across projects. If an equivalent image already exists, set its image name in `.env`.

Validate the reranker images after building them:

```powershell
docker run --rm text-reranker-runtime:cpu python -c "import torch; print(torch.__version__)"
docker run --rm --gpus all text-reranker-runtime:cuda python -c "import torch; print(torch.cuda.is_available())"
```

The CUDA check should print `True`. If it does not, verify the NVIDIA Driver and NVIDIA Container Toolkit first.

## System Python Tests

The API test suite can run with system Python. Install the API dependencies first from `apps/api`:

```powershell
cd apps/api
python -m pip install -e ".[dev]"
python -m pytest
```

This path is for local unit tests; Docker runtime diagnostics should still use the container environment.

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

The launcher reads the repository-root `.env`, chooses the CPU/CUDA reranker path from `RERANKER_ENABLED` and `RERANKER_DEVICE`, and rewrites the API container's PostgreSQL, Qdrant, Redis and reranker URLs to Docker-network service names. Do not copy the README's host-side `localhost` database URLs into compose; compose already uses `postgres`, `qdrant`, `redis` and `reranker` inside the Docker network.

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

## Docker End-to-End Smoke Test

After startup, run a disposable-course smoke test for the API, dependent services, database, Qdrant, reranker and real model calls:

```powershell
python scripts/docker_smoke.py --base-url http://127.0.0.1:8000/api
```

The script creates a temporary course, uploads Markdown, parses it, runs search, runs QA, reads the session, and deletes the course. The `/api/search` response must report `model_audit.embedding_external_called=true` and `embedding_fallback_reason=null` to prove that retrieval did not use fallback.

To check only Docker infrastructure connectivity:

```powershell
python scripts/docker_smoke.py --skip-model-calls
```

## API Endpoints

The FastAPI OpenAPI schema is exposed at `/openapi.json`. The table below follows the current schema and frontend call chain.

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
| POST | `/api/search` | Hybrid search with `model_audit` for query embedding calls |
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
