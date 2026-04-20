from datetime import date, datetime, timezone, timedelta

import redis.asyncio as aioredis
from core.config import settings

_redis: aioredis.Redis | None = None

TIER_LIMITS = {
    "anonymous": 300,    # 5 credits/week
    "free": 300,         # 5 credits/week
    "fan": 3600,         # 60 credits/week
    "super_fan": 15000,  # 250 credits/week
}

SECONDS_PER_CREDIT = 60

# Credits are the user-facing unit; internally we track seconds.
def seconds_to_credits(seconds: int) -> int:
    import math
    if seconds < 0:
        return -1
    return math.ceil(seconds / SECONDS_PER_CREDIT)


def limit_to_credits(limit_seconds: int) -> int:
    if limit_seconds == -1:
        return -1
    return limit_seconds // SECONDS_PER_CREDIT


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        # Bug #21: db=1 was a code-override because REDIS_URL had /0; URL now uses /1 consistently
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _quota_key(user_id: str | None, device_id: str | None) -> str:
    today = date.today()
    iso = today.isocalendar()
    week_key = f"{iso.year}W{iso.week:02d}"
    if user_id:
        return f"quota:{user_id}:{week_key}"
    return f"quota:anon:{device_id}:{week_key}"


def _seconds_until_week_end_ist() -> int:
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    # ISO week ends Sunday; next Monday 00:00 IST is the reset point
    days_until_monday = 7 - now.weekday()  # weekday(): Mon=0 … Sun=6
    next_monday = (now + timedelta(days=days_until_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int((next_monday - now).total_seconds())


async def get_quota_remaining(user_id: str | None, device_id: str | None, tier: str) -> int:
    limit = TIER_LIMITS.get(tier, 300)
    if limit == -1:
        return -1
    r = get_redis()
    used = int(await r.get(_quota_key(user_id, device_id)) or 0)
    return max(0, limit - used)


async def add_quota_usage(user_id: str | None, device_id: str | None, seconds: int) -> int:
    r = get_redis()
    key = _quota_key(user_id, device_id)
    new_total = await r.incrby(key, seconds)
    await r.expire(key, _seconds_until_week_end_ist())
    return new_total


async def blacklist_jwt(jti: str, ttl_seconds: int) -> None:
    r = get_redis()
    await r.setex(f"jwt:blacklist:{jti}", ttl_seconds, "1")


async def is_jwt_blacklisted(jti: str) -> bool:
    r = get_redis()
    return bool(await r.exists(f"jwt:blacklist:{jti}"))
