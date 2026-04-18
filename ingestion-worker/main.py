import asyncio
import uuid
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline import run_pipeline
from qdrant_writer import toggle_chunks
from transcript import extract_video_id


class Settings(BaseSettings):
    database_url: str
    qdrant_url: str
    qdrant_collection: str = "rgv_knowledge"
    embedding_service_url: str
    redis_url: str
    anthropic_api_key: str
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
_pool: asyncpg.Pool | None = None
_jobs: dict[str, asyncio.Task] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    yield
    await _pool.close()


app = FastAPI(title="Ask RGV Ingestion Worker", lifespan=lifespan)


class IngestRequest(BaseModel):
    url: str
    language: str


class BulkIngestRequest(BaseModel):
    urls: list[str]
    language: str


class ToggleRequest(BaseModel):
    enabled: bool


@app.get("/health")
def health():
    from transcript import whisper_loaded
    return {"status": "ok", "whisper_loaded": whisper_loaded()}


@app.post("/ingest/single")
async def ingest_single(body: IngestRequest):
    # Bug #16: main.py generated a new UUID and returned it as job_id, but pipeline.py's
    # ON CONFLICT kept the original row's id — client's job_id didn't exist in DB (404 on status).
    # Fix: look up existing row by video_id first so returned job_id always matches DB.
    video_id = extract_video_id(body.url)
    existing = await _pool.fetchrow("SELECT id FROM ingestion_log WHERE video_id = $1", video_id)
    job_id = str(existing["id"]) if existing else str(uuid.uuid4())

    task = asyncio.create_task(
        run_pipeline(job_id, body.url, body.language, _pool, settings)
    )
    _jobs[job_id] = task
    return {"job_id": job_id}


@app.post("/ingest/bulk")
async def ingest_bulk(body: BulkIngestRequest):
    job_ids = []
    for url in body.urls:
        video_id = extract_video_id(url)
        existing = await _pool.fetchrow("SELECT id FROM ingestion_log WHERE video_id = $1", video_id)
        job_id = str(existing["id"]) if existing else str(uuid.uuid4())
        task = asyncio.create_task(
            run_pipeline(job_id, url, body.language, _pool, settings)
        )
        _jobs[job_id] = task
        job_ids.append(job_id)
    return {"job_ids": job_ids}


@app.get("/ingest/job/{job_id}")
async def job_status(job_id: str):
    if _pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    row = await _pool.fetchrow("SELECT * FROM ingestion_log WHERE id = $1", uuid.UUID(job_id))
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(row)


@app.patch("/ingest/{job_id}/toggle")
async def toggle_job(job_id: str, body: ToggleRequest):
    if _pool is None:
        raise HTTPException(status_code=503, detail="DB not ready")
    row = await _pool.fetchrow("SELECT video_id FROM ingestion_log WHERE id = $1", uuid.UUID(job_id))
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    await toggle_chunks(row["video_id"], body.enabled, settings)
    await _pool.execute(
        "UPDATE ingestion_log SET enabled = $1 WHERE id = $2",
        body.enabled, uuid.UUID(job_id),
    )
    return {"status": "ok"}
