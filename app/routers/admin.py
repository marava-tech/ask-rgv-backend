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
from db.pool import get_pool
from models.schemas import (
    AdminLoginRequest,
    BugReportUpdateRequest,
    IngestBulkRequest,
    IngestSingleRequest,
    QuoteCreateRequest,
    ToggleRequest,
)
from services import config_service

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

    import pyotp

    want_email = settings.admin_dashboard_email.strip().lower()
    got_email = body.email.strip().lower()
    email_ok = got_email == want_email

    # TOTP path (preferred): verify 6-digit code with ±1 window tolerance
    if settings.admin_totp_secret:
        totp = pyotp.TOTP(settings.admin_totp_secret)
        # valid_window=1 allows the code from the previous or next 30-second window
        code_ok = totp.verify(body.totp_code.strip(), valid_window=1)
    else:
        # Legacy fallback: treat totp_code field as plain password
        code_ok = secrets.compare_digest(body.totp_code, settings.admin_password)

    if not (email_ok and code_ok):
        await r.incr(rate_key)
        await r.expire(rate_key, ADMIN_LOGIN_WINDOW_SECONDS)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or TOTP code",
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
async def persona_run(request: Request):
    asyncio.create_task(_run_persona_qa(getattr(request.app.state, "prompt_loader", None)))
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
    chunks_by_topic: dict[str, list[dict]] = {}

    for topic, query in TEST_QUERIES:
        try:
            chunks = await search_chunks(query)
            if not chunks:
                scores_by_topic[topic] = [0.0]
                chunks_by_topic[topic] = []
                continue
            chunk_text = chunks[0]["payload"].get("text", "")[:400]
            score_str = await haiku_call(
                f"Query: {query}\nChunk: {chunk_text}\n\nIs this chunk relevant? Score 0.0-1.0. Reply with only a number."
            )
            relevance = max(0.0, min(1.0, float(score_str.strip())))
            scores_by_topic[topic] = [relevance]
            chunks_by_topic[topic] = [
                {
                    "text": c["payload"].get("text", "")[:300],
                    "source_video_id": c["payload"].get("source_video_id", ""),
                    "qdrant_score": round(c.get("score", 0.0), 4),
                }
                for c in chunks[:3]
            ]
        except Exception:
            scores_by_topic[topic] = [0.5]
            chunks_by_topic[topic] = []

    topic_scores = {t: sum(s) / len(s) for t, s in scores_by_topic.items()}
    overall = sum(topic_scores.values()) / len(topic_scores)
    passed = overall >= 0.75

    report = dict(topic_scores)
    report["_chunks"] = chunks_by_topic

    pool = get_pool()
    await pool.execute(
        "INSERT INTO validation_runs (overall_score, passed, report_json) VALUES ($1, $2, $3)",
        overall, passed, json.dumps(report),
    )


async def _run_persona_qa(loader=None):
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
            messages, system_blocks = await assemble_prompt("debating", [], [], "", "en", q, "default", loader=loader)
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
        overall, passed, json.dumps(report),
    )


# ── App Config ────────────────────────────────────────────────────────────────

@router.get("/config")
async def list_config(_: dict = Depends(require_admin)):
    return await config_service.get_all_config_rows()


@router.patch("/config/{key}")
async def update_config(key: str, body: dict, _: dict = Depends(require_admin)):
    value = body.get("value")
    if value is None:
        raise HTTPException(status_code=400, detail="value required")
    await config_service.set_config(key, str(value))
    return {"key": key, "value": str(value)}


# ── Response Monitor ──────────────────────────────────────────────────────────

@router.post("/monitor/run")
async def run_monitor(_: dict = Depends(require_admin)):
    asyncio.create_task(_run_monitor_bg())
    return {"status": "started"}


@router.get("/monitor/latest")
async def get_latest_monitor(_: dict = Depends(require_admin)):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id, sample_size, report_json, improvement_notes, run_at FROM monitor_runs ORDER BY run_at DESC LIMIT 1"
    )
    if not row:
        return {"status": "no_runs"}
    return dict(row)


async def _run_monitor_bg(sample_size: int = 50):
    from services.response_monitor import run_monitor as _run
    try:
        await _run(sample_size)
    except Exception:
        pass


@router.get("/users", dependencies=[Depends(require_admin)])
async def list_users(limit: int = 100, offset: int = 0):
    users = await queries.list_admin_users(limit=limit, offset=offset)
    return {"users": users, "limit": limit, "offset": offset}


@router.post("/users/{user_id}/reset-quota", dependencies=[Depends(require_admin)])
async def reset_user_quota(user_id: str):
    """Delete the current week's quota key for a user so their credits reset immediately."""
    from services.quota import delete_quota_key
    user = await queries.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await delete_quota_key(user_id)
    return {"status": "ok", "user_id": user_id}


@router.get("/bug-reports")
async def list_bug_reports(
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    _: dict = Depends(require_admin),
):
    items = await queries.get_bug_reports(limit=limit, offset=offset, status=status)
    total = await queries.count_bug_reports(status=status)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/bug-reports/{bug_id}")
async def get_bug_report(bug_id: int, _: dict = Depends(require_admin)):
    row = await queries.get_bug_report_by_id(bug_id)
    if not row:
        raise HTTPException(status_code=404, detail="Bug report not found")
    return row


@router.patch("/bug-reports/{bug_id}")
async def update_bug_report(
    bug_id: int,
    body: BugReportUpdateRequest,
    admin_payload: dict = Depends(require_admin),
):
    updated = await queries.update_bug_report(
        bug_id,
        status=body.status,
        admin_notes=body.admin_notes,
        resolved_by=admin_payload.get("sub"),
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Bug report not found")
    return updated


@router.get("/waitlist/signups", dependencies=[Depends(require_admin)])
async def waitlist_signups(
    limit: int = 100,
    offset: int = 0,
    search: str | None = None,
):
    pool = get_pool()
    if search:
        rows = await pool.fetch(
            """
            SELECT ws.id, ws.name, ws.email, ws.language, ws.is_rgv_fan,
                   ws.app_promo_code, ws.merch_promo_code, ws.created_at,
                   wmi.categories AS merch_interest
            FROM waitlist_signups ws
            LEFT JOIN waitlist_merch_interest wmi ON wmi.waitlist_id = ws.id
            WHERE ws.name ILIKE $3 OR ws.email ILIKE $3
            ORDER BY ws.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
            f"%{search}%",
        )
    else:
        rows = await pool.fetch(
            """
            SELECT ws.id, ws.name, ws.email, ws.language, ws.is_rgv_fan,
                   ws.app_promo_code, ws.merch_promo_code, ws.created_at,
                   wmi.categories AS merch_interest
            FROM waitlist_signups ws
            LEFT JOIN waitlist_merch_interest wmi ON wmi.waitlist_id = ws.id
            ORDER BY ws.created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit,
            offset,
        )
    return {"signups": [dict(r) for r in rows], "limit": limit, "offset": offset}


@router.get("/waitlist/interests", dependencies=[Depends(require_admin)])
async def waitlist_interests():
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT unnest(categories) AS category, count(*) AS cnt
        FROM waitlist_merch_interest
        GROUP BY category
        """
    )
    counts = {r["category"]: r["cnt"] for r in rows}
    categories = ["tshirt", "hoodie", "poster", "mug", "not_sure"]
    return {cat: counts.get(cat, 0) for cat in categories}


# ── Prompt Config endpoints ────────────────────────────────────────────────────

@router.get("/prompts", dependencies=[Depends(require_admin)])
async def list_prompts():
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT key, label, description, version, updated_at FROM prompt_configs ORDER BY key"
    )
    return [dict(r) for r in rows]


@router.get("/prompts/{key}", dependencies=[Depends(require_admin)])
async def get_prompt(key: str):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT key, label, description, content, version, updated_at FROM prompt_configs WHERE key = $1",
        key,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Prompt key '{key}' not found")
    return dict(row)


@router.put("/prompts/{key}", dependencies=[Depends(require_admin)])
async def update_prompt(key: str, body: dict, request: Request):
    content = body.get("content", "")
    if not content or not content.strip():
        raise HTTPException(status_code=422, detail="content must be a non-empty string")
    if len(content) > 16000:
        raise HTTPException(status_code=422, detail="content must be ≤ 16,000 characters")

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT version FROM prompt_configs WHERE key = $1", key
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Prompt key '{key}' not found")

    new_version = row["version"] + 1
    updated = await pool.fetchrow(
        """
        UPDATE prompt_configs
           SET content = $1, version = $2, updated_at = now()
         WHERE key = $3
        RETURNING key, label, description, content, version, updated_at
        """,
        content, new_version, key,
    )
    await pool.execute(
        """
        INSERT INTO prompt_config_history (prompt_key, content, version)
        VALUES ($1, $2, $3)
        """,
        key, content, new_version,
    )

    loader = getattr(request.app.state, "prompt_loader", None)
    if loader is not None:
        await loader.invalidate(key)

    return dict(updated)


@router.get("/prompts/{key}/history", dependencies=[Depends(require_admin)])
async def get_prompt_history(key: str):
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT version, content, changed_at
          FROM prompt_config_history
         WHERE prompt_key = $1
         ORDER BY changed_at DESC
         LIMIT 50
        """,
        key,
    )
    return [dict(r) for r in rows]


# ── Ingestion Chunk Inspector ─────────────────────────────────────────────────

@router.get("/ingestion/{video_id}/chunks", dependencies=[Depends(require_admin)])
async def list_ingestion_chunks(video_id: str):
    from core.config import settings as _settings
    qdrant_url = f"{_settings.qdrant_url}/collections/{_settings.qdrant_collection}/points/scroll"
    payload = {
        "filter": {"must": [{"key": "video_id", "match": {"value": video_id}}]},
        "limit": 100,
        "with_payload": True,
        "with_vector": False,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(qdrant_url, json=payload)
            r.raise_for_status()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Qdrant unreachable: {type(e).__name__}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e.response.status_code}")

    points = r.json().get("result", {}).get("points", [])
    chunks = [
        {
            "point_id": str(p["id"]),
            "text_preview": p["payload"].get("text", "")[:400],
            "quality_score": p["payload"].get("quality_score", 0.0),
            "enabled": p["payload"].get("enabled", True),
        }
        for p in points
    ]
    chunks.sort(key=lambda c: c["quality_score"], reverse=True)
    return chunks


@router.patch("/ingestion/{video_id}/chunks/{point_id}/toggle", dependencies=[Depends(require_admin)])
async def toggle_ingestion_chunk(video_id: str, point_id: str, body: ToggleRequest):
    from core.config import settings as _settings
    qdrant_url = f"{_settings.qdrant_url}/collections/{_settings.qdrant_collection}/points/payload"
    payload = {
        "payload": {"enabled": body.enabled},
        "points": [point_id],
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(qdrant_url, json=payload)
            r.raise_for_status()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Qdrant unreachable: {type(e).__name__}")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e.response.status_code}")
    return {"point_id": point_id, "enabled": body.enabled}


# ── Style Profile Extraction ──────────────────────────────────────────────────

@router.post("/style-profiles/extract/{video_id}", dependencies=[Depends(require_admin)], status_code=202)
async def extract_style_profiles(video_id: str):
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM ingestion_log WHERE video_id = $1 AND status = 'complete'",
        video_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Video not found or not complete")
    from services.style_profile_extraction import extract_style_profiles_for_video
    asyncio.create_task(extract_style_profiles_for_video(video_id))
    return {"status": "started", "video_id": video_id}


# ── Ad-hoc RAG Search ─────────────────────────────────────────────────────────

@router.post("/rag/search", dependencies=[Depends(require_admin)])
async def admin_rag_search(body: dict):
    query = (body.get("query") or "").strip()
    if not query:
        raise HTTPException(status_code=422, detail="query must not be empty")
    video_id_filter = body.get("video_id")
    limit = min(int(body.get("limit") or 10), 20)

    from services.embedding import embed_query
    try:
        embedding = await embed_query(query)
    except Exception:
        raise HTTPException(status_code=503, detail="Embedding service unavailable")

    from core.config import settings as _settings
    search_url = f"{_settings.qdrant_url}/collections/{_settings.qdrant_collection}/points/search"

    must_filters: list[dict] = [{"key": "enabled", "match": {"value": True}}]
    if video_id_filter:
        must_filters.append({"key": "video_id", "match": {"value": video_id_filter}})
    qdrant_filter = {"must": must_filters}

    dense_payload = {
        "vector": {"name": "dense", "vector": embedding["dense"]},
        "filter": qdrant_filter,
        "limit": limit,
        "with_payload": True,
    }
    sparse_payload = {
        "vector": {
            "name": "sparse",
            "vector": {
                "indices": [int(i) for i in embedding["sparse"]["indices"]],
                "values": embedding["sparse"]["values"],
            },
        },
        "filter": qdrant_filter,
        "limit": limit,
        "with_payload": True,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            dense_r, sparse_r = await asyncio.gather(
                client.post(search_url, json=dense_payload),
                client.post(search_url, json=sparse_payload),
            )
            dense_r.raise_for_status()
            sparse_r.raise_for_status()
    except httpx.RequestError:
        raise HTTPException(status_code=503, detail="Qdrant unreachable")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Qdrant error: {e.response.status_code}")

    dense_results = dense_r.json().get("result", [])
    sparse_results = sparse_r.json().get("result", [])

    # RRF fusion
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}
    k = 60
    for rank, item in enumerate(dense_results):
        pid = str(item["id"])
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank + 1)
        payloads[pid] = item["payload"]
    for rank, item in enumerate(sparse_results):
        pid = str(item["id"])
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank + 1)
        payloads.setdefault(pid, item["payload"])

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [
        {
            "point_id": pid,
            "text": payloads[pid].get("text", ""),
            "video_id": payloads[pid].get("video_id"),
            "source_title": payloads[pid].get("source_title"),
            "qdrant_score": round(score, 4),
        }
        for pid, score in ranked
    ]


# ── Conversations Admin ───────────────────────────────────────────────────────

@router.get("/conversations", dependencies=[Depends(require_admin)])
async def list_conversations():
    sessions = await queries.get_admin_conversations(limit=50)
    return sessions


@router.get("/conversations/{session_id}", dependencies=[Depends(require_admin)])
async def get_conversation(session_id: str):
    detail = await queries.get_admin_conversation_detail(session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Session not found")
    return detail
