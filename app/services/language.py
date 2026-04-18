import redis.asyncio as aioredis
from core.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def detect_language(text: str) -> str:
    telugu_count = sum(1 for ch in text if "\u0C00" <= ch <= "\u0C7F")
    hindi_count = sum(1 for ch in text if "\u0900" <= ch <= "\u097F")
    total = len(text.strip())
    if total == 0:
        return "en"
    if telugu_count / total > 0.15:
        return "te"
    if hindi_count / total > 0.15:
        return "hi"
    return "en"


async def get_session_language(session_id: str) -> str | None:
    r = get_redis()
    return await r.get(f"session:lang:{session_id}")


async def set_session_language(session_id: str, language: str) -> None:
    r = get_redis()
    await r.setex(f"session:lang:{session_id}", 1800, language)
