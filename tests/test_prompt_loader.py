"""Unit tests for PromptLoader fallback chain."""
import pytest


class _FailingRedis:
    async def get(self, key):
        raise ConnectionError("Redis unavailable")

    async def set(self, *a, **kw):
        raise ConnectionError("Redis unavailable")

    async def delete(self, key):
        raise ConnectionError("Redis unavailable")


@pytest.mark.asyncio
async def test_loader_falls_back_to_constants_when_db_and_redis_unavailable(monkeypatch):
    """When both Redis and DB are unavailable, loader returns hardcoded fallback."""
    import sys
    import types

    # Stub out services.quota so PromptLoader can import it without a running Redis
    quota_stub = types.ModuleType("services.quota")
    quota_stub.get_redis = lambda: _FailingRedis()
    monkeypatch.setitem(sys.modules, "services.quota", quota_stub)

    # Import after patching
    from importlib import import_module, invalidate_caches
    invalidate_caches()

    # Direct import — pool is None, Redis raises, so fallback is used
    from services.prompt_loader import PromptLoader, _FALLBACKS

    loader = PromptLoader()
    # Inject failing redis directly
    loader._redis = _FailingRedis()
    loader._pool = None  # No DB pool

    result = await loader.get("system_persona")
    assert result == _FALLBACKS["system_persona"]
    assert len(result) > 50


@pytest.mark.asyncio
async def test_loader_returns_empty_string_for_unknown_key(monkeypatch):
    import sys
    import types

    quota_stub = types.ModuleType("services.quota")
    quota_stub.get_redis = lambda: _FailingRedis()
    monkeypatch.setitem(sys.modules, "services.quota", quota_stub)

    from services.prompt_loader import PromptLoader

    loader = PromptLoader()
    loader._redis = _FailingRedis()
    loader._pool = None

    result = await loader.get("nonexistent_key_xyz")
    assert result == ""
