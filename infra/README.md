# Infra

Docker Compose runs the full local stack:

- API on `localhost:8000`
- Web on `localhost:3000`
- PostgreSQL on `localhost:5432`
- Redis on `localhost:6379`
- Qdrant on `localhost:6333`

## Images

The launcher does not build images. Build the default CPU stack images from the repo root:

```powershell
docker build -f apps/api/Dockerfile -t course-kg-api:local .
docker build -f apps/web/Dockerfile -t course-kg-web:local .
```

For CUDA reranking, first check whether you already have a compatible CUDA API image. If you do, set `API_CUDA_IMAGE` in `.env`. Otherwise build the project default:

```powershell
docker build -f apps/api/Dockerfile.cuda -t course-kg-api-cuda:local .
```

## Run

Use the repo-root launcher:

```powershell
.\start-app.ps1
```

The launcher reads `RERANKER_DEVICE` from `.env`:

- `cpu`: starts the CPU API profile
- `cuda`: starts the CUDA API profile and requires NVIDIA Container Toolkit

Direct Compose examples:

```powershell
docker compose -f infra/docker-compose.yml --profile api-cpu up -d
docker compose -f infra/docker-compose.yml -f infra/docker-compose.cuda.yml --profile api-cuda up -d
```
