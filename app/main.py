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
from routers import admin, admin_merch, auth, conversation, feedback, merch, notifications, quotes, session, subscription, user, voice_stream, waitlist, webhooks

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


async def _cleanup_empty_sessions():
    """Runs every 3 hours — deletes sessions with no turns older than 3 hours.
    These show as 'Conversation' (null title) in the history screen and represent
    abandoned sessions where the user opened but never spoke. Covers both authenticated
    and anonymous sessions."""
    try:
        from db.pool import get_pool
        pool = get_pool()
        result = await pool.execute(
            """
            DELETE FROM sessions
            WHERE turn_count = 0
              AND started_at < now() - interval '3 hours'
            """
        )
        logger.info("[scheduler] empty_session_cleanup: %s", result)
    except Exception as e:
        logger.error("[scheduler] empty_session_cleanup error: %s", e)


async def _cleanup_empty_sessions_with_lock():
    await _with_leader_lock("empty_session_cleanup", _cleanup_empty_sessions, ttl=10790)


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


async def _daily_broadcast():
    """Sends today's RGV provocation to all users who have registered an FCM token."""
    from datetime import date
    import httpx
    import google.auth.transport.requests as google_requests
    from google.oauth2 import service_account
    from data.daily_provocations import DAILY_PROVOCATIONS
    from db import queries

    day_index = (date.today().timetuple().tm_yday - 1) % len(DAILY_PROVOCATIONS)
    provocation = DAILY_PROVOCATIONS[day_index]

    fcm_tokens = await queries.get_all_fcm_tokens()
    if not fcm_tokens:
        logger.info("[daily-broadcast] no FCM tokens, skipping")
        return

    try:
        creds = service_account.Credentials.from_service_account_file(
            settings.firebase_service_account_path,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        google_requests.Request()(creds)
    except Exception as e:
        logger.error("[daily-broadcast] Firebase credential load failed: %s", e)
        return

    sent = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for token in fcm_tokens:
            try:
                resp = await client.post(
                    "https://fcm.googleapis.com/v1/projects/askrgv/messages:send",
                    headers={"Authorization": f"Bearer {creds.token}"},
                    json={
                        "message": {
                            "token": token,
                            "notification": {
                                "title": "RGV has something to say.",
                                "body": provocation,
                            },
                            "android": {
                                "priority": "normal",
                                "notification": {"click_action": "OPEN_CONVERSATION"},
                            },
                        }
                    },
                )
                if resp.status_code == 200:
                    sent += 1
                else:
                    logger.warning("[daily-broadcast] FCM rejected token: %s", resp.text[:200])
            except Exception as e:
                logger.warning("[daily-broadcast] send error: %s", e)

    logger.info("[daily-broadcast] sent=%d/%d provocation_index=%d", sent, len(fcm_tokens), day_index)


async def _daily_broadcast_with_lock():
    await _with_leader_lock("daily_broadcast", _daily_broadcast, ttl=3590)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()

    from db.migrations import run_pending
    from db.pool import get_pool
    await run_pending(get_pool())

    scheduler.add_job(_nightly_cleanup_with_lock, CronTrigger(hour=0, minute=0))
    scheduler.add_job(_cleanup_empty_sessions_with_lock, IntervalTrigger(hours=3))
    scheduler.add_job(_expire_subscriptions_with_lock, IntervalTrigger(hours=12))
    scheduler.add_job(_daily_broadcast_with_lock, CronTrigger(hour=9, minute=0))
    scheduler.start()

    # Load all AI prompts from DB into Redis so first conversation turn uses DB persona.
    try:
        from services.prompt_loader import PromptLoader
        prompt_loader = PromptLoader()
        await prompt_loader.load_all(get_pool())
        app.state.prompt_loader = prompt_loader
    except Exception as e:
        logger.warning("[startup] prompt_loader init failed (non-fatal): %s", e)
        app.state.prompt_loader = None

    # Pre-synthesize static phrases (crisis, greeting, thinking) so first playback is instant.
    try:
        from services.phrase_cache import warmup_phrase_cache
        await warmup_phrase_cache()
    except Exception as e:
        logger.warning("[startup] phrase cache warmup failed (non-fatal): %s", e)

    # Seed Anthropic ephemeral cache with static persona block for all 3 languages.
    # First real user turn then gets a cache hit — saves ~300 ms TTFT.
    try:
        from services.cache_warmer import warm_prompt_cache
        await warm_prompt_cache()
    except Exception as e:
        logger.warning("[startup] prompt cache warmup failed (non-fatal): %s", e)

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
    max_age=600,
)

app.include_router(auth.router)
app.include_router(conversation.router)
app.include_router(session.router)
app.include_router(user.router)
app.include_router(subscription.router)
app.include_router(notifications.router)
app.include_router(quotes.router)
app.include_router(admin.router)
app.include_router(waitlist.router)
app.include_router(merch.router)
app.include_router(admin_merch.router)
app.include_router(webhooks.router)
app.include_router(voice_stream.router)
app.include_router(feedback.router)


async def _probe_http(url: str, timeout: float = 5.0) -> dict[str, str | bool]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
        return {"ok": True, "url": url}
    except httpx.RequestError as e:
        return {
            "ok": False,
            "url": url,
            "error_type": type(e).__name__,
            "error": str(e),
        }
    except httpx.HTTPStatusError as e:
        return {
            "ok": False,
            "url": url,
            "error_type": type(e).__name__,
            "status_code": str(e.response.status_code),
            "error": e.response.text[:500],
        }


@app.get("/health")
async def health():
    checks: dict[str, object] = {}

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

    qdrant_url = f"{settings.qdrant_url}/collections"
    embedding_url = f"{settings.embedding_service_url}/health"
    checks["qdrant"] = await _probe_http(qdrant_url)
    checks["embedding"] = await _probe_http(embedding_url)

    def _is_ok(value: object) -> bool:
        return value == "ok" or (isinstance(value, dict) and value.get("ok") is True)

    overall = "ok" if all(_is_ok(v) for v in checks.values()) else "degraded"
    return {"status": overall, **checks}
