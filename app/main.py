import httpx
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from db.pool import close_pool, init_pool
from routers import admin, auth, conversation, quotes, subscription, voice

scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")


async def _reset_daily_quotas():
    """Runs at midnight IST — Redis keys expire naturally, nothing to do.
    This job cleans up expired refresh tokens and old sessions from Postgres."""
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
        print(f"[scheduler] quota reset error: {e}")


async def _send_quote_of_day():
    """Runs at 9 AM IST — sends FCM push notification with today's quote."""
    try:
        from db.pool import get_pool
        pool = get_pool()
        quote = await pool.fetchrow(
            "SELECT text, source FROM quotes WHERE active = true ORDER BY random() LIMIT 1"
        )
        if not quote:
            return
        users = await pool.fetch(
            "SELECT id FROM users WHERE tier IN ('fan', 'super_fan')"
        )
        if not users:
            return
        print(f"[scheduler] quote-of-day sent to {len(users)} users: {quote['text'][:60]}...")
    except Exception as e:
        print(f"[scheduler] quote-of-day error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    scheduler.add_job(_reset_daily_quotas, CronTrigger(hour=0, minute=0))
    scheduler.add_job(_send_quote_of_day, CronTrigger(hour=9, minute=0))
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    await close_pool()


app = FastAPI(title="Ask RGV API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ask-rgv-dashboard.marava.tech",
        "http://localhost:3000",
        "http://localhost:3700",
    ],
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
        r = aioredis.from_url(settings.redis_url)
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
