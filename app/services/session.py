import json
import redis.asyncio as aioredis
from core.config import settings

_redis: aioredis.Redis | None = None
SESSION_TTL = 1800
MAX_HISTORY_TURNS = 8


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True, db=1)
    return _redis


async def get_history(session_id: str) -> list[dict]:
    r = get_redis()
    raw = await r.get(f"session:{session_id}")
    if not raw:
        return []
    return json.loads(raw)


async def append_turn(session_id: str, user_input: str, response: str) -> None:
    r = get_redis()
    history = await get_history(session_id)
    history.append({"role": "user", "content": user_input})
    history.append({"role": "assistant", "content": response})
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
    await r.setex(f"session:{session_id}", SESSION_TTL, json.dumps(history))


async def clear_session(session_id: str) -> None:
    r = get_redis()
    await r.delete(f"session:{session_id}", f"session:lang:{session_id}")
