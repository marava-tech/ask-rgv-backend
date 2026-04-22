import logging
import httpx
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from db.pool import close_pool, init_pool
from routers import admin, auth, conversation, feedback, quotes, subscription, voice

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


async def _with_leader_lock(name: str, fn, ttl: int = 290) -> None:
    """Runs fn only if this worker acquires a Redis SETNX lock — prevents duplicate
    scheduler executions when multiple Gunicorn workers each start their own scheduler.
    Bug #13: with WORKERS=4, four schedulers fired the same job four times at midnight."""
    from services.quota import get_redis
    r = get_redis()
    lock_key = f"scheduler:lock:{name}"
    acquired = await r.set(lock_key, "1", nx=True, ex=ttl)
    if not acquired:
        return
    try:
        await fn()
    finally:
        await r.delete(lock_key)


async def _nightly_cleanup():
    """Runs at midnight IST — cleans up expired refresh tokens and old sessions.

    Refresh rows use expires_at (rolling window for mobile clients, see REFRESH_TOKEN_TTL in core.auth).
    Bug #27: was named _reset_daily_quotas but never touched quotas (they expire via Redis TTL)."""
    try:
        from db.pool import get_pool
        pool = get_pool()
        await pool.execute("DELETE FROM refresh_tokens WHERE expires_at < now()")
        await pool.execute(
            "DELETE FROM sessions WHERE user_id IS NOT NULL AND started_at < now() - interval '90 days'"
        )
        await pool.execute(
            "DELETE FROM crisis_events WHERE timestamp < now() - interval '1 year'"
        )
    except Exception as e:
        logger.error("[scheduler] nightly_cleanup error: %s", e)


async def _nightly_cleanup_with_lock():
    await _with_leader_lock("nightly_cleanup", _nightly_cleanup)


async def _delete_empty_recent_sessions():
    """Runs every hour — deletes sessions with no turns started in the last 61 minutes.
    The extra minute is a clock-skew buffer so sessions at the top of the hour aren't missed.
    Covers both authenticated and anonymous sessions."""
    try:
        from db.pool import get_pool
        pool = get_pool()
        result = await pool.execute(
            """
            DELETE FROM sessions
            WHERE turn_count = 0
              AND started_at BETWEEN now() - interval '61 minutes' AND now()
            """
        )
        logger.info("[scheduler] empty_session_cleanup: %s", result)
    except Exception as e:
        logger.error("[scheduler] empty_session_cleanup error: %s", e)


async def _delete_empty_recent_sessions_with_lock():
    await _with_leader_lock("empty_session_cleanup", _delete_empty_recent_sessions, ttl=17900)


async def _expire_subscriptions():
    """Runs every hour — downgrades users whose 30-day plan period has ended.
    Updates both the subscriptions row (status → 'expired') and users.tier
    in a single atomic transaction."""
    try:
        from db import queries
        count = await queries.expire_ended_subscriptions()
        if count > 0:
            logger.info("[scheduler] expire_subscriptions: downgraded %d user(s) to free", count)
    except Exception as e:
        logger.error("[scheduler] expire_subscriptions error: %s", e)


async def _expire_subscriptions_with_lock():
    await _with_leader_lock("expire_subscriptions", _expire_subscriptions, ttl=43100)


# Bug #14: quote-of-day scheduler only printed; no FCM implementation existed.
# Bug #28: quotes weren't grouped by user language.
# Removed until FCM + user device tokens are implemented (Phase 8).


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    scheduler.add_job(_nightly_cleanup_with_lock, CronTrigger(hour=0, minute=0))
    scheduler.add_job(_delete_empty_recent_sessions_with_lock, IntervalTrigger(hours=5))
    scheduler.add_job(_expire_subscriptions_with_lock, IntervalTrigger(hours=12))
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    await close_pool()


app = FastAPI(title="Ask RGV API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(conversation.router)
app.include_router(subscription.router)
app.include_router(quotes.router)
app.include_router(admin.router)
app.include_router(voice.router)
app.include_router(feedback.router)


@app.get("/health")
async def health():
    checks: dict[str, str] = {}

    try:
        from db.pool import get_pool
        pool = get_pool()
        await pool.fetchval("SELECT 1")
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"

    try:
        import redis.asyncio as aioredis
        # Bug #20: health check used DB 0 while the rest of the app uses DB 1;
        # Redis DB 1 could be broken while health reported green
        r = aioredis.from_url(settings.redis_url, db=1)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.qdrant_url}/collections")
            r.raise_for_status()
        checks["qdrant"] = "ok"
    except Exception as e:
        checks["qdrant"] = f"error: {e}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.embedding_service_url}/health")
            r.raise_for_status()
        checks["embedding"] = "ok"
    except Exception as e:
        checks["embedding"] = f"error: {e}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, **checks}
