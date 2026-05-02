# Infrastructure

The local stack is Docker-first and split into reusable infrastructure plus small project images.

## Services

- `api`: project FastAPI image, `course-kg-api:local`
- `web`: project Next.js image, `course-kg-web:local`
- `postgres`: reusable `postgres:16`
- `redis`: reusable `redis:7`
- `qdrant`: reusable `qdrant/qdrant:v1.13.2`
- `reranker-cpu`: reusable `text-reranker-runtime:cpu`
- `reranker-cuda`: reusable `text-reranker-runtime:cuda`

PostgreSQL must stay on major version 16 because the existing data directory has `PG_VERSION=16`.

## Validate Existing Images

If these reusable images already exist on your machine, validate them and skip rebuilding:

```powershell
docker run --rm postgres:16 postgres --version
docker run --rm redis:7 redis-server --version
docker image inspect qdrant/qdrant:v1.13.2
docker run --rm text-reranker-runtime:cpu python -c "import torch; print(torch.__version__)"
docker run --rm --gpus all text-reranker-runtime:cuda python -c "import torch; print(torch.cuda.is_available())"
```

## Build Missing Images

Build only the images you do not already have:

```powershell
docker build -f apps/api/Dockerfile -t course-kg-api:local .
docker build -f apps/web/Dockerfile -t course-kg-web:local .
docker build -f infra/reranker/Dockerfile.cpu -t text-reranker-runtime:cpu infra/reranker
docker build -f infra/reranker/Dockerfile.cuda -t text-reranker-runtime:cuda infra/reranker
```

## Run

From the repository root:

```powershell
.\start-app.ps1
```

The launcher reads `.env`:

- `RERANKER_DEVICE=cpu`: starts `reranker-cpu`
- `RERANKER_DEVICE=cuda`: starts `reranker-cuda`

Direct Compose examples:

```powershell
docker compose -f infra/docker-compose.yml --profile reranker-cpu up -d postgres redis qdrant reranker-cpu api web
docker compose -f infra/docker-compose.yml -f infra/docker-compose.cuda.yml --profile reranker-cuda up -d postgres redis qdrant reranker-cuda api web
```
