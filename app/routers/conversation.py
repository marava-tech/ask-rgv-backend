import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from core.auth import get_current_user
from db import queries
from models.schemas import (
    EndSessionRequest,
    InterruptRequest,
    RenameSessionRequest,
    SessionData,
    StartSessionRequest,
    StartSessionResponse,
    TurnRequest,
    UsageRequest,
)
from services import session as session_svc
from services.claude import haiku_call, sonnet_stream

_log = logging.getLogger(__name__)


async def _generate_title(session_id: str, user_input: str) -> None:
    try:
        title = await haiku_call(
            f'Generate a concise 3-5 word title for a conversation that started with: "{user_input}". '
            'Return ONLY the title, no quotes, no punctuation at the end.'
        )
        title = title.strip().strip('"').strip("'")[:80]
        if title:
            await queries.update_session_title(session_id, title)
    except Exception as e:
        _log.warning("[title] generation failed for session %s: %r", session_id, e)
from services.crisis import detect_crisis, get_safety_response
from services.intent import classify_intent
from services.language import detect_language, get_session_language, set_session_language
from services.prompt import assemble_prompt, estimate_turn_duration
from services.qdrant_search import search_chunks
from services.quota import TIER_LIMITS, add_quota_usage, get_quota_remaining
from services.style_profiles import get_style_anchors

import asyncio

router = APIRouter(prefix="/conversation", tags=["conversation"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/start", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None
    # Bug #9: anonymous sessions were not persisted — created random UUID in memory only,
    # so crisis logging and end_session were silent no-ops for anon users
    session_id = await queries.create_session(user_id, body.device_id)
    return StartSessionResponse(session=SessionData(
        id=session_id,
        mode=body.mode,
        # Bug #39: language isn't known until first turn — removed misleading "en" default
        started_at=datetime.now(timezone.utc).isoformat(),
    ))


@router.post("/turn")
async def conversation_turn(body: TurnRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None
    device_id = body.device_id

    # Bug #6: device_id=None collapsed all anon users into one quota:anon:None:date key
    if not user_id and not device_id:
        raise HTTPException(status_code=400, detail="device_id required for anonymous sessions")

    async def event_stream():
        # Bug #8: tier was read from the stale JWT (up to 1h old); look up current tier from DB
        if user_id:
            user_data = await queries.get_user_by_id(user_id)
            tier = user_data["tier"] if user_data else "free"
        else:
            tier = "anonymous"

        quota_remaining = await get_quota_remaining(user_id, device_id, tier)
        if quota_remaining == 0:
            yield _sse("quota_exhausted", {"tier": tier})
            return

        # Resolve language from cache; persist to DB on first detection
        lang = await get_session_language(body.session_id)
        if lang is None:
            lang = detect_language(body.message)
            await set_session_language(body.session_id, lang)
            # Bug #11: language only lived in Redis (30-min TTL) and could flip after expiry;
            # persist to DB so session language survives Redis eviction
            if user_id:
                await queries.update_session_language(body.session_id, lang)

        is_crisis, trigger = detect_crisis(body.message)
        if is_crisis:
            asyncio.create_task(
                queries.log_crisis_event(body.session_id, trigger)
            )
            yield _sse("safety", {"text": get_safety_response(lang)})
            yield _sse("done", {})
            return

        history, rag_chunks, style_anchors, intent = await asyncio.gather(
            session_svc.get_history(body.session_id),
            search_chunks(body.message),
            get_style_anchors(lang),
            classify_intent(body.message),
        )

        messages, system_blocks = assemble_prompt(
            intent=intent,
            history=history,
            rag_chunks=rag_chunks,
            style_anchors=style_anchors,
            language=lang,
            user_input=body.message,
            mode=body.mode,
        )

        start_time = time.time()
        full_response = ""
        # Bug #12: usage_out dict collects token counts from sonnet_stream after streaming completes
        usage_out: dict = {}

        try:
            async for token in sonnet_stream(messages, system_blocks, usage_out):
                full_response += token
                yield _sse("token", {"text": token})
        except Exception as e:
            # Bug #24: str(e) could leak API key from httpx error URL; log server-side, send generic code
            import logging
            logging.getLogger(__name__).error("[turn] stream error: %r", e)
            if full_response and user_id:
                # Persist partial response so history isn't lost on stream failure
                await session_svc.append_turn(body.session_id, body.message, full_response)
            yield _sse("error", {"code": "STREAM_ERROR"})
            return

        latency_ms = int((time.time() - start_time) * 1000)
        turn_seconds = estimate_turn_duration(full_response)
        tokens_used = usage_out.get("input_tokens", 0) + usage_out.get("output_tokens", 0)

        # Bug #10: store_turn was fire-and-forget via create_task so turn_id was never returned;
        # await it now so we can yield turn_id to the client before done
        turn_id = None
        if user_id:
            turn_id, turn_count = await queries.store_turn(
                session_id=body.session_id,
                mode=body.mode,
                user_input=body.message,
                response=full_response,
                tokens_used=tokens_used,
                latency_ms=latency_ms,
                rag_chunks_used=len(rag_chunks),
            )
            await session_svc.append_turn(body.session_id, body.message, full_response)
            yield _sse("turn_id", {"id": turn_id})
            if turn_count == 1:
                asyncio.create_task(_generate_title(body.session_id, body.message))

        new_used = await add_quota_usage(user_id, device_id, turn_seconds)
        limit = TIER_LIMITS.get(tier, 300)

        if limit != -1 and new_used >= limit:
            yield _sse("quota_exhausted", {"tier": tier, "upgrade_url": "/subscription"})

        yield _sse("done", {
            "latency_ms": latency_ms,
            "rag_chunks": len(rag_chunks),
            "turn_seconds": turn_seconds,
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/usage", status_code=204)
async def record_usage(body: UsageRequest, user: dict | None = Depends(get_current_user)):
    if user and body.turn_id:
        # Bug #5: update was unconstrained — pass user_id so only the turn's owner can update it
        await queries.update_turn_audio_seconds(body.turn_id, body.played_seconds, user["sub"])
    user_id = user["sub"] if user else None
    await add_quota_usage(user_id, body.device_id, body.played_seconds)


@router.post("/interrupt", status_code=204)
async def interrupt_turn(body: InterruptRequest, user: dict | None = Depends(get_current_user)):
    if user and body.active_turn_id:
        # Bug #5: same ownership constraint applied here
        await queries.update_turn_audio_seconds(body.active_turn_id, body.played_seconds, user["sub"])
    user_id = user["sub"] if user else None
    await add_quota_usage(user_id, body.device_id, body.played_seconds)


@router.post("/end", status_code=204)
async def end_session(body: EndSessionRequest, user: dict | None = Depends(get_current_user)):
    if user:
        await queries.end_session(body.session_id)
    await session_svc.clear_session(body.session_id)


@router.get("/sessions")
async def conversation_history(
    limit: int = 20,
    offset: int = 0,
    q: str | None = None,
    user: dict = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    sessions = await queries.get_user_sessions(user["sub"], limit, offset, q)
    return [dict(s) for s in sessions]


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    deleted = await queries.delete_session(session_id, user["sub"])
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    user: dict = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    renamed = await queries.rename_session(session_id, user["sub"], body.title)
    if not renamed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return {"id": session_id, "title": body.title}


@router.get("/sessions/{session_id}/turns")
async def get_session_detail(session_id: str, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    # Bug #4: no ownership check — any user could read any other user's session turns
    session = await queries.get_session(session_id)
    if not session or (session["user_id"] and str(session["user_id"]) != user["sub"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    turns = await queries.get_session_turns(session_id)
    return [dict(t) for t in turns]
