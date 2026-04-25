"""Pre-synthesized audio cache for static phrases (crisis, greetings, etc.)."""
import logging

import redis.asyncio as aioredis

from core.config import settings
from services.crisis import SAFETY_RESPONSE

_log = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None

PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "crisis_safety": SAFETY_RESPONSE["en"],
        "thinking": "Let me think about that for a moment.",
        "greeting": "Hey. What's on your mind?",
    },
    "te": {
        "crisis_safety": SAFETY_RESPONSE["te"],
        "thinking": "ఒక్క నిమిషం ఆలోచిస్తాను.",
        "greeting": "చెప్పండి. ఏమి అడగాలనుకుంటున్నారు?",
    },
    "hi": {
        "crisis_safety": SAFETY_RESPONSE["hi"],
        "thinking": "एक पल सोचता हूँ।",
        "greeting": "बताइए। क्या पूछना है?",
    },
}


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=False)
    return _redis


def _key(lang: str, slug: str) -> str:
    return f"phrase:{lang}:{slug}"


async def get_cached_phrase(lang: str, slug: str) -> bytes | None:
    r = _get_redis()
    try:
        return await r.get(_key(lang, slug))
    except Exception as e:
        _log.warning("[phrase_cache] get failed lang=%s slug=%s: %r", lang, slug, e)
        return None


async def _cache_phrase(lang: str, slug: str, wav: bytes) -> None:
    r = _get_redis()
    try:
        await r.set(_key(lang, slug), wav)
    except Exception as e:
        _log.warning("[phrase_cache] set failed lang=%s slug=%s: %r", lang, slug, e)


async def warmup_phrase_cache() -> None:
    """Called at startup to pre-synthesize and cache all static phrases per language."""
    if not settings.smallest_ai_api_key:
        _log.info("[phrase_cache] skipping warmup — SMALLEST_AI_API_KEY not set")
        return

    from services.tts import synthesise_sentence

    for lang, phrases in PHRASES.items():
        for slug, text in phrases.items():
            existing = await get_cached_phrase(lang, slug)
            if existing:
                continue
            try:
                wav = await synthesise_sentence(text, lang)
                await _cache_phrase(lang, slug, wav)
                _log.info("[phrase_cache] warmed lang=%s slug=%s bytes=%d", lang, slug, len(wav))
            except Exception as e:
                _log.warning("[phrase_cache] warmup failed lang=%s slug=%s: %r", lang, slug, e)
