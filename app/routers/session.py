import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth import get_current_user, require_user
from data.greetings import get_greeting
from db.pool import get_pool
from services.claude import haiku_call

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/session", tags=["session"])


class GreetingResponse(BaseModel):
    greeting: str


class HighlightResponse(BaseModel):
    highlight_text: str


@router.get("/greeting", response_model=GreetingResponse)
async def session_greeting(
    name: str,
    session_num: int = 1,
    tier: str = "free",
    lang: str = "en",
    user: dict | None = Depends(get_current_user),
):
    if lang not in ("en", "te", "hi"):
        lang = "en"

    greeting = get_greeting(
        name=name or "there",
        session_num=session_num,
        tier=tier,
        lang=lang,
    )
    return GreetingResponse(greeting=greeting)


@router.post("/{session_id}/highlight", response_model=HighlightResponse)
async def session_highlight(
    session_id: str,
    user: dict = Depends(require_user),
):
    pool = get_pool()

    try:
        sid = UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid session_id")

    row = await pool.fetchrow(
        "SELECT id FROM sessions WHERE id = $1 AND user_id = $2",
        sid, UUID(user["sub"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    turns = await pool.fetch(
        """
        SELECT user_input, response
        FROM turns
        WHERE session_id = $1
        ORDER BY created_at
        LIMIT 30
        """,
        sid,
    )

    if not turns:
        raise HTTPException(status_code=422, detail="Session has no turns")

    conversation = "\n".join(
        f"User: {t['user_input']}\nRGV: {t['response']}" for t in turns
    )

    prompt = (
        "Extract the single most provocative or memorable thing RGV said in this conversation. "
        "One sentence, max 120 characters. Return only the quote text, no quotation marks.\n\n"
        f"Conversation:\n{conversation}"
    )

    try:
        highlight = await haiku_call(prompt)
        highlight = highlight.strip()[:120]
    except Exception as e:
        logger.error("[highlight] haiku_call failed for session %s: %s", session_id, e)
        raise HTTPException(status_code=503, detail="Highlight extraction failed")

    await pool.execute(
        "UPDATE sessions SET highlight_text = $1 WHERE id = $2",
        highlight, sid,
    )

    return HighlightResponse(highlight_text=highlight)
