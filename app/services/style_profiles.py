import json
import redis.asyncio as aioredis
from core.config import settings
from db import queries

_redis: aioredis.Redis | None = None
CACHE_TTL = 3600


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_style_anchors(language: str) -> str:
    r = get_redis()
    cache_key = f"style:anchors:{language}"
    cached = await r.get(cache_key)
    if cached:
        return cached

    quotes = await queries.get_style_profiles(language, limit=20)
    if not quotes:
        return ""

    block = "[STYLE_ANCHORS]\n" + "\n".join(f"- {q}" for q in quotes)
    await r.setex(cache_key, CACHE_TTL, block)
    return block
