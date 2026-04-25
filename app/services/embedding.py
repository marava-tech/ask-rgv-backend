import hashlib
import json
import logging

import httpx
import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)

_http_client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
_redis: aioredis.Redis | None = None

_EMBED_CACHE_TTL = 3600  # 1 hour


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


def _cache_key(text: str) -> str:
    h = hashlib.sha256(text.strip().lower().encode()).hexdigest()[:24]
    return f"embed:q:{h}"


async def embed_texts(texts: list[str], batch_size: int = 16) -> list[dict]:
    url = f"{settings.embedding_service_url}/embed"
    try:
        response = await _http_client.post(
            url,
            json={"texts": texts, "batch_size": batch_size},
        )
        response.raise_for_status()
        data = response.json()
        return [
            {"dense": data["dense"][i], "sparse": data["sparse"][i]}
            for i in range(len(texts))
        ]
    except httpx.RequestError as e:
        logger.exception("[embed] request failed url=%s error=%r", url, e)
        raise
    except httpx.HTTPStatusError as e:
        logger.exception(
            "[embed] bad response url=%s status=%s body=%r",
            url,
            e.response.status_code,
            e.response.text[:500],
        )
        raise


async def embed_query(text: str) -> dict:
    if not settings.embed_cache_enabled or len(text.strip()) < 4:
        results = await embed_texts([text])
        return results[0]

    key = _cache_key(text)
    r = _get_redis()
    try:
        cached = await r.get(key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("[embed] cache read failed: %r", e)

    results = await embed_texts([text])
    embedding = results[0]

    try:
        await r.setex(key, _EMBED_CACHE_TTL, json.dumps(embedding))
    except Exception as e:
        logger.warning("[embed] cache write failed: %r", e)

    return embedding
