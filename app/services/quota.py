import time
from datetime import date, datetime, timezone, timedelta

import redis.asyncio as aioredis
from core.config import settings

_redis: aioredis.Redis | None = None

# Fallback limits (seconds/week) used only when app_config is unavailable
_DEFAULT_TIER_LIMITS = {
    "anonymous": 300,
    "free": 300,
    "fan": 3600,
    "super_fan": -1,
}

_TIER_CONFIG_KEYS = {
    "anonymous": "anonymous_weekly_credits",
    "free": "free_weekly_credits",
    "fan": "fan_weekly_credits",
    "super_fan": "super_fan_weekly_credits",
}

SECONDS_PER_CREDIT = 60

# B-12: module-level cache so get_tier_limit_seconds() doesn't round-trip Redis per request
_tier_limit_cache: dict[str, tuple[int, float]] = {}
_TIER_LIMIT_CACHE_TTL = 60.0

# B-03/B-06/BO-05: atomic capped INCRBY + conditional EXPIRE in one Redis round-trip.
# ARGV: [to_add, limit (-1=unlimited), ttl_seconds]
_QUOTA_INCRBY_LUA = """
local key = KEYS[1]
local to_add = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local exists = redis.call("EXISTS", key)
local current = tonumber(redis.call("GET", key) or "0")
local add = to_add
if limit >= 0 then
  if current >= limit then return current end
  if (current + to_add) > limit then add = limit - current end
end
local new_val = redis.call("INCRBY", key, add)
if exists == 0 then redis.call("EXPIRE", key, ttl) end
return new_val
"""

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


def next_credit_refresh_utc() -> datetime:
    """Return the next weekly credit reset as a UTC datetime."""
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    days_until_monday = 7 - now.weekday()
    next_monday_ist = (now + timedelta(days=days_until_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return next_monday_ist.astimezone(timezone.utc)


async def get_tier_limit_seconds(tier: str) -> int:
    """Return weekly seconds limit for a tier, reading from app_config with 60s in-memory cache."""
    now = time.monotonic()
    cached = _tier_limit_cache.get(tier)
    if cached and (now - cached[1]) < _TIER_LIMIT_CACHE_TTL:
        return cached[0]

    from services.config_service import get_config_int
    config_key = _TIER_CONFIG_KEYS.get(tier)
    result = _DEFAULT_TIER_LIMITS.get(tier, 300)
    if config_key:
        credits = await get_config_int(config_key, default=-999)
        if credits != -999:
            result = -1 if credits < 0 else credits * SECONDS_PER_CREDIT

    _tier_limit_cache[tier] = (result, now)
    return result


async def get_quota_remaining(user_id: str | None, device_id: str | None, tier: str) -> int:
    limit = await get_tier_limit_seconds(tier)
    if limit == -1:
        return -1
    r = get_redis()
    used = int(await r.get(_quota_key(user_id, device_id)) or 0)
    return max(0, limit - used)


async def add_quota_usage(
    user_id: str | None, device_id: str | None, seconds: int, limit: int = -1
) -> int:
    """Add usage seconds atomically, capped at limit (-1 = unlimited). Returns new total used."""
    r = get_redis()
    key = _quota_key(user_id, device_id)
    ttl = _seconds_until_week_end_ist()
    new_total = await r.eval(_QUOTA_INCRBY_LUA, 1, key, seconds, limit, ttl)
    return int(new_total)


async def delete_quota_key(user_id: str) -> None:
    """Delete the current week's quota key for a user (used by admin reset-quota)."""
    r = get_redis()
    await r.delete(_quota_key(user_id, None))


async def blacklist_jwt(jti: str, ttl_seconds: int) -> None:
    r = get_redis()
    await r.setex(f"jwt:blacklist:{jti}", ttl_seconds, "1")


async def is_jwt_blacklisted(jti: str) -> bool:
    r = get_redis()
    return bool(await r.exists(f"jwt:blacklist:{jti}"))
