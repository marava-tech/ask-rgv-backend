from fastapi import APIRouter, Query
from db import queries

router = APIRouter(prefix="/quotes", tags=["quotes"])


@router.get("/today")
async def quote_of_day(language: str = Query(default="en", pattern="^(en|te|hi)$")):
    quote = await queries.get_quote_of_day(language)
    if not quote:
        return {"text": None, "source": None, "language": language}
    return {**quote, "language": language}
