import asyncio
import json
import secrets
import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi import Form as FastAPIForm
from fastapi.responses import JSONResponse

from core.auth import create_admin_token, require_admin
from core.config import settings
from db import queries
from models.schemas import (
    AdminLoginRequest,
    IngestBulkRequest,
    IngestSingleRequest,
    QuoteCreateRequest,
    ToggleRequest,
)

router = APIRouter(prefix="/admin", tags=["admin"])

ADMIN_LOGIN_MAX_ATTEMPTS = 5
ADMIN_LOGIN_WINDOW_SECONDS = 900  # 15 minutes


def _normalize_validation_report(report_json):
    if isinstance(report_json, str):
        try:
            parsed = json.loads(report_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return report_json if isinstance(report_json, dict) else {}


@router.post("/auth/login")
async def admin_login(request: Request, body: AdminLoginRequest):
    from services.quota import get_redis

    # Bug #23: no rate limiting — unlimited brute-force password attempts
    r = get_redis()
    forwarded = request.headers.get("X-Forwarded-For", "")
    client_host = request.client.host if request.client else ""
    client_ip = (forwarded or client_host).split(",")[0].strip() or "unknown"
    rate_key = f"admin:login:fail:{client_ip}"

    fails = int(await r.get(rate_key) or 0)
    if fails >= ADMIN_LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts — try again in 15 minutes",
        )

    want_email = settings.admin_dashboard_email.strip().lower()
    got_email = body.email.strip().lower()
    # Always run password compare_digest so response time does not reveal email validity alone.
    password_ok = secrets.compare_digest(body.password, settings.admin_password)
    email_ok = got_email == want_email
    if not (email_ok and password_ok):
        await r.incr(rate_key)
        await r.expire(rate_key, ADMIN_LOGIN_WINDOW_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    await r.delete(rate_key)
    return {"admin_token": create_admin_token()}


@router.get("/stats", dependencies=[Depends(require_admin)])
async def admin_stats():
    return await queries.get_admin_stats()


@router.get("/ingestion/list", dependencies=[Depends(require_admin)])
async def list_ingestion_jobs():
    jobs = await queries.list_ingestion_jobs()
    return {"jobs": jobs}


@router.post("/ingestion/single", dependencies=[Depends(require_admin)])
async def ingest_single(body: IngestSingleRequest):
    # Bug #40: r.raise_for_status() raised httpx.HTTPStatusError → unhandled 500
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{settings.ingestion_worker_url}/ingest/single",
                json={"url": body.url, "language": body.language},
            )
            r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker error: {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker unreachable: {type(e).__name__}")


@router.post("/ingestion/bulk", dependencies=[Depends(require_admin)])
async def ingest_bulk(body: IngestBulkRequest):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"{settings.ingestion_worker_url}/ingest/bulk",
                json={"urls": body.urls, "language": body.language},
            )
            r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker error: {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker unreachable: {type(e).__name__}")


@router.post("/ingestion/transcript", dependencies=[Depends(require_admin)])
async def ingest_transcript(
    url: str = FastAPIForm(...),
    language: str = FastAPIForm(...),
    file: UploadFile = File(...),
):
    content = await file.read()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{settings.ingestion_worker_url}/ingest/transcript",
                data={"url": url, "language": language},
                files={"file": (file.filename, content, file.content_type or "application/octet-stream")},
            )
            r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker error: {e.response.text}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker unreachable: {type(e).__name__}")


@router.get("/ingestion/job/{job_id}", dependencies=[Depends(require_admin)])
async def ingestion_job_status(job_id: str):
    job = await queries.get_ingestion_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.patch("/ingestion/{job_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_ingestion(job_id: str, body: ToggleRequest):
    await queries.toggle_ingestion_job(job_id, body.enabled)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.patch(
                f"{settings.ingestion_worker_url}/ingest/{job_id}/toggle",
                json={"enabled": body.enabled},
            )
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker error: {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Ingestion worker unreachable: {type(e).__name__}")
    return {"status": "ok"}


@router.get("/quotes", dependencies=[Depends(require_admin)])
async def list_quotes_admin(language: str | None = None):
    if language is not None and language not in ("en", "te", "hi"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="language must be en, te, or hi when provided",
        )
    quotes = await queries.list_quotes_for_admin(language=language)
    return {"quotes": quotes}


@router.post("/quotes", dependencies=[Depends(require_admin)])
async def create_quote(body: QuoteCreateRequest):
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Quote text cannot be empty")
    quote = await queries.insert_quote(text=text, source=body.source, language=body.language)
    return {"quote": quote}


@router.patch("/quotes/{quote_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_quote(quote_id: str, body: ToggleRequest):
    await queries.set_quote_active(quote_id, body.enabled)
    return {"status": "ok"}


@router.get("/style-profiles", dependencies=[Depends(require_admin)])
async def list_style_profiles(language: str = "en"):
    from db.pool import get_pool
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, language, quote, source_video_id, active, created_at FROM rgv_style_profiles WHERE language = $1 ORDER BY active DESC, created_at DESC",
        language,
    )
    return {"profiles": [dict(r) for r in rows]}


@router.patch("/style-profiles/{profile_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_style_profile(profile_id: str, body: ToggleRequest):
    from db.pool import get_pool
    from uuid import UUID
    pool = get_pool()
    await pool.execute(
        "UPDATE rgv_style_profiles SET active = $1 WHERE id = $2",
        body.enabled, UUID(profile_id),
    )
    return {"status": "ok"}


@router.get("/validation/latest", dependencies=[Depends(require_admin)])
async def validation_latest():
    from db.pool import get_pool
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM validation_runs ORDER BY run_at DESC LIMIT 1"
    )
    if not row:
        raise HTTPException(status_code=404, detail="No validation runs yet")
    data = dict(row)
    data["report_json"] = _normalize_validation_report(data.get("report_json"))
    return data


@router.post("/validation/run", dependencies=[Depends(require_admin)], status_code=202)
async def validation_run():
    asyncio.create_task(_run_rag_validation())
    return {"status": "started"}


@router.post("/validation/persona-run", dependencies=[Depends(require_admin)], status_code=202)
async def persona_run():
    asyncio.create_task(_run_persona_qa())
    return {"status": "started"}


async def _run_rag_validation():
    from services.qdrant_search import search_chunks
    from services.claude import haiku_call
    from db.pool import get_pool

    TEST_QUERIES = [
        ("fear", "What does RGV say about fear?"),
        ("love", "RGV on love and relationships"),
        ("death", "RGV views on death and mortality"),
        ("success", "What is RGV's definition of success?"),
        ("failure", "How does RGV view failure?"),
        ("creativity", "RGV on creativity and filmmaking"),
        ("money", "RGV on money and wealth"),
        ("reality", "RGV views on reality vs illusion"),
        ("courage", "RGV on courage and risk-taking"),
        ("ego", "RGV on ego and identity"),
    ]

    scores_by_topic: dict[str, list[float]] = {}
    for topic, query in TEST_QUERIES:
        try:
            chunks = await search_chunks(query)
            if not chunks:
                scores_by_topic[topic] = [0.0]
                continue
            chunk_text = chunks[0]["payload"].get("text", "")[:400]
            score_str = await haiku_call(
                f"Query: {query}\nChunk: {chunk_text}\n\nIs this chunk relevant? Score 0.0-1.0. Reply with only a number."
            )
            scores_by_topic[topic] = [max(0.0, min(1.0, float(score_str.strip())))]
        except Exception:
            scores_by_topic[topic] = [0.5]

    topic_scores = {t: sum(s) / len(s) for t, s in scores_by_topic.items()}
    overall = sum(topic_scores.values()) / len(topic_scores)
    passed = overall >= 0.75

    pool = get_pool()
    await pool.execute(
        "INSERT INTO validation_runs (overall_score, passed, report_json) VALUES ($1, $2, $3)",
        overall, passed, topic_scores,
    )


async def _run_persona_qa():
    from services.claude import sonnet_stream
    from services.prompt import assemble_prompt
    from services.claude import haiku_call
    from db.pool import get_pool

    QUESTIONS = [
        "I believe hard work always leads to success.",
        "I feel like a failure.",
        "Money is the root of all evil.",
        "I need validation from others to feel good.",
        "I think I'm special and destined for greatness.",
    ]

    EVAL_PROMPT = """Evaluate this AI response playing Ram Gopal Varma.
Question: {q}
Response: {r}

Score each (1-5):
- directness: Does it give a direct opinion?
- philosophical_accuracy: Does it reflect RGV's known philosophy?
- tone: Is it blunt and contrarian?
- consistency: Is it consistent with the persona?
- no_cliches: Does it avoid comfort/clichés?

Reply as JSON only: {{"directness":X,"philosophical_accuracy":X,"tone":X,"consistency":X,"no_cliches":X,"issue":"brief issue or empty string"}}"""

    dim_scores: dict[str, list[float]] = {d: [] for d in ["directness", "philosophical_accuracy", "tone", "consistency", "no_cliches"]}
    flagged = []

    for q in QUESTIONS:
        try:
            messages, system_blocks = assemble_prompt("debating", [], [], "", "en", q, "default")
            response = ""
            async for token in sonnet_stream(messages, system_blocks):
                response += token

            eval_result = await haiku_call(EVAL_PROMPT.format(q=q, r=response[:500]))
            scores = json.loads(eval_result.strip())
            for dim in dim_scores:
                dim_scores[dim].append(float(scores.get(dim, 3)))
            if any(float(scores.get(d, 3)) < 3.0 for d in dim_scores):
                flagged.append({"question": q, "answer": response[:200], "issue": scores.get("issue", "Low score")})
        except Exception:
            for dim in dim_scores:
                dim_scores[dim].append(3.0)

    report = {d: sum(v) / len(v) for d, v in dim_scores.items()}
    report["flagged"] = flagged
    overall = sum(report[d] for d in ["directness", "philosophical_accuracy", "tone", "consistency", "no_cliches"]) / 5
    passed = overall >= 3.5

    pool = get_pool()
    await pool.execute(
        "INSERT INTO validation_runs (overall_score, passed, report_json) VALUES ($1, $2, $3)",
        overall, passed, report,
    )
