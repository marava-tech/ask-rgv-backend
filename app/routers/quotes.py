import json

from fastapi import APIRouter, Query
from db import queries
from services.quota import get_redis, _seconds_until_midnight_ist

router = APIRouter(prefix="/quotes", tags=["quotes"])

_QUOTE_TODAY_KEY = "quote:today:{language}"
_QUOTE_LAST_KEY = "quote:last:{language}"


@router.get("/today")
async def quote_of_day(language: str = Query(default="en", pattern="^(en|te|hi)$")):
    r = get_redis()
    today_key = _QUOTE_TODAY_KEY.format(language=language)
    cached = await r.get(today_key)
    if cached:
        return json.loads(cached)

    last_key = _QUOTE_LAST_KEY.format(language=language)
    last_id = await r.get(last_key)

    quote = await queries.get_quote_of_day_except(language, last_id)
    if not quote:
        quote = await queries.get_quote_of_day(language)
    if not quote:
        return {"text": None, "source": None, "language": language}

    payload = {"text": quote["text"], "source": quote["source"], "language": language}
    ttl = _seconds_until_midnight_ist()
    await r.setex(today_key, ttl, json.dumps(payload))
    await r.setex(last_key, 48 * 3600, str(quote["id"]))
    return payload
