import json
from services.quota import get_redis
from db.pool import get_pool

_CONFIG_CACHE_KEY = "config:all"
_CONFIG_TTL = 600  # 10 minutes


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


async def set_config(key: str, value: str) -> None:
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
