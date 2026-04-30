import json
import logging

import redis.asyncio as aioredis

from core.config import settings

_log = logging.getLogger(__name__)
_redis: aioredis.Redis | None = None
SESSION_TTL = 1800

# Bug #29: constant was duplicated (8 here, 16 in prompt.py); single source of truth
MAX_HISTORY_TURNS = 8

_TITLE_LANG_INSTRUCTION: dict[str, str] = {
    "te": "Generate the title in Telugu using Telugu script (తెలుగు).",
    "hi": "Generate the title in Hindi using Devanagari script (हिंदी).",
}


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def get_history(session_id: str) -> list[dict]:
    r = get_redis()
    key = f"session:{session_id}"
    raw_items = await r.lrange(key, 0, -1)
    return [json.loads(item) for item in raw_items]


async def append_turn(session_id: str, user_input: str, response: str) -> None:
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


async def generate_title(session_id: str, user_input: str, lang: str = "en") -> None:
    from db import queries
    from services.claude import haiku_call
    try:
        lang_instruction = _TITLE_LANG_INSTRUCTION.get(lang, "Generate the title in English.")
        title = await haiku_call(
            f'Generate a concise 3-5 word title for a conversation that started with: "{user_input}". '
            f'{lang_instruction} '
            'Return ONLY the title, no quotes, no punctuation at the end.'
        )
        title = title.strip().strip('"').strip("'")[:80]
        if title:
            await queries.update_session_title(session_id, title)
    except Exception as e:
        _log.warning("[session] title generation failed for %s: %r", session_id, e)


async def update_user_memory(user_id: str, user_input: str, rgv_response: str) -> None:
    """Post-turn: Haiku summarizes the exchange and upserts user_memory for Super Fan."""
    from db import queries
    from services.claude import haiku_call
    try:
        existing = await queries.get_user_memory(user_id)
        existing_summary = existing["summary"] if existing else ""
        existing_facts = existing["key_facts"] if existing else {}

        prompt = (
            f"Existing memory summary:\n{existing_summary or '(none yet)'}\n\n"
            f"New exchange:\nUser: {user_input}\nRGV: {rgv_response}\n\n"
            "Update the memory summary (2-4 sentences, first-person about the user) and extract/update "
            "key facts as a JSON object with string keys. "
            "Return ONLY valid JSON: {\"summary\": \"...\", \"key_facts\": {...}}"
        )
        raw = await haiku_call(prompt)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        data = json.loads(raw)
        summary = str(data.get("summary", existing_summary))[:2000]
        key_facts = data.get("key_facts", existing_facts)
        if not isinstance(key_facts, dict):
            key_facts = existing_facts
        await queries.upsert_user_memory(user_id, summary, key_facts)
    except Exception as e:
        _log.warning("[session] user_memory update failed for %s: %r", user_id, e)
