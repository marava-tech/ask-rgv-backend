import redis.asyncio as aioredis
from core.config import settings

_redis: aioredis.Redis | None = None
SESSION_TTL = 1800

# Bug #29: constant was duplicated (8 here, 16 in prompt.py); single source of truth
MAX_HISTORY_TURNS = 8


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_history(session_id: str) -> list[dict]:
    r = get_redis()
    key = f"session:{session_id}"
    raw_items = await r.lrange(key, 0, -1)
    import json
    return [json.loads(item) for item in raw_items]


async def append_turn(session_id: str, user_input: str, response: str) -> None:
    import json
    r = get_redis()
    key = f"session:{session_id}"
    pipe = r.pipeline()
    # Bug #38: prior read-modify-write had a race — concurrent turns could both read the same
    # history and last-write-wins would drop a turn; RPUSH+LTRIM is atomic via pipeline
    pipe.rpush(key, json.dumps({"role": "user", "content": user_input}))
    pipe.rpush(key, json.dumps({"role": "assistant", "content": response}))
    pipe.ltrim(key, -(MAX_HISTORY_TURNS * 2), -1)
    pipe.expire(key, SESSION_TTL)
    await pipe.execute()


async def clear_session(session_id: str) -> None:
    r = get_redis()
    await r.delete(f"session:{session_id}", f"session:lang:{session_id}")
