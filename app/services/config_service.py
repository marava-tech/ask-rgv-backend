import json
import time as _time
from services.quota import get_redis
from db.pool import get_pool

_CONFIG_CACHE_KEY = "config:all"
_CONFIG_TTL = 600  # 10 minutes

# BO-04: in-memory cache for full config rows (including description/updated_at).
# Avoids a Postgres round-trip on every admin dashboard poll (every 30 s).
_full_rows_cache: tuple[list[dict], float] | None = None
# Short TTL so multi-worker drift is bounded to 5 s; Redis is authoritative.
_FULL_ROWS_CACHE_TTL = 5.0


async def get_all_config() -> dict[str, str]:
    r = get_redis()
    cached = await r.get(_CONFIG_CACHE_KEY)
    if cached:
        return json.loads(cached)

    pool = get_pool()
    rows = await pool.fetch("SELECT key, value FROM app_config ORDER BY key")
    config = {row["key"]: row["value"] for row in rows}
    await r.setex(_CONFIG_CACHE_KEY, _CONFIG_TTL, json.dumps(config))
    return config


async def get_config(key: str, default: str = "") -> str:
    config = await get_all_config()
    return config.get(key, default)


async def get_config_int(key: str, default: int = 0) -> int:
    val = await get_config(key, str(default))
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


async def get_all_config_rows() -> list[dict]:
    """Return full config rows (key, value, description, updated_at) with 30 s in-memory cache."""
    global _full_rows_cache
    now = _time.monotonic()
    if _full_rows_cache and (now - _full_rows_cache[1]) < _FULL_ROWS_CACHE_TTL:
        return _full_rows_cache[0]
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT key, value, description, updated_at FROM app_config ORDER BY key"
    )
    result = [dict(r) for r in rows]
    _full_rows_cache = (result, now)
    return result


async def set_config(key: str, value: str) -> None:
    global _full_rows_cache
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO app_config (key, value, updated_at)
        VALUES ($1, $2, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
        """,
        key, value,
    )
    r = get_redis()
    await r.delete(_CONFIG_CACHE_KEY)
    _full_rows_cache = None  # invalidate in-memory cache
