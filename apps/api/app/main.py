from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router
from app.db import ensure_schema
from app.services.ingestion import finalize_interrupted_batches


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_schema()
    finalize_interrupted_batches()
    yield


app = FastAPI(title="Course Knowledge Base API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router, prefix="/api")
