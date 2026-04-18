from datetime import date, datetime, timezone, timedelta

import redis.asyncio as aioredis
from core.config import settings

_redis: aioredis.Redis | None = None

TIER_LIMITS = {
    "anonymous": 300,
    "free": 300,
    "fan": 3600,
    "super_fan": -1,
}


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _quota_key(user_id: str | None, device_id: str | None) -> str:
    today = date.today().isoformat()
    if user_id:
        return f"quota:{user_id}:{today}"
    return f"quota:anon:{device_id}:{today}"


def _seconds_until_midnight_ist() -> int:
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((midnight - now).total_seconds())


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
    await r.expire(key, _seconds_until_midnight_ist())
    return new_total


async def blacklist_jwt(jti: str, ttl_seconds: int) -> None:
    r = get_redis()
    await r.setex(f"jwt:blacklist:{jti}", ttl_seconds, "1")


async def is_jwt_blacklisted(jti: str) -> bool:
    r = get_redis()
    return bool(await r.exists(f"jwt:blacklist:{jti}"))
