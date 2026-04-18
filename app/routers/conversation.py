import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from core.auth import get_current_user
from db import queries
from models.schemas import (
    EndSessionRequest,
    InterruptRequest,
    SessionData,
    StartSessionRequest,
    StartSessionResponse,
    TurnRequest,
    UsageRequest,
)
from services import session as session_svc
from services.claude import sonnet_stream
from services.crisis import detect_crisis, get_safety_response
from services.intent import classify_intent
from services.language import detect_language, get_session_language, set_session_language
from services.prompt import assemble_prompt, estimate_turn_duration
from services.qdrant_search import search_chunks
from services.quota import TIER_LIMITS, add_quota_usage, get_quota_remaining
from services.style_profiles import get_style_anchors

router = APIRouter(prefix="/conversation", tags=["conversation"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/start", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None
    if user_id:
        session_id = await queries.create_session(user_id, body.device_id)
    else:
        import uuid
        session_id = str(uuid.uuid4())
    return StartSessionResponse(session=SessionData(
        id=session_id,
        mode=body.mode,
        language="en",
        started_at=datetime.now(timezone.utc).isoformat(),
    ))


@router.post("/turn")
async def conversation_turn(body: TurnRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None
    tier = user.get("tier", "anonymous") if user else "anonymous"
    device_id = body.device_id

    async def event_stream():
        quota_remaining = await get_quota_remaining(user_id, device_id, tier)
        if quota_remaining == 0:
            yield _sse("quota_exhausted", {"tier": tier})
            return

        # Resolve language from cache before crisis check so safety responses use correct language
        lang = await get_session_language(body.session_id)
        if lang is None:
            lang = detect_language(body.message)
            await set_session_language(body.session_id, lang)

        is_crisis, trigger = detect_crisis(body.message)
        if is_crisis:
            asyncio.create_task(
                queries.log_crisis_event(body.session_id if user_id else None, trigger)
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
        tokens_used = 0

        try:
            async for token in sonnet_stream(messages, system_blocks):
                full_response += token
                yield _sse("token", {"text": token})
        except Exception as e:
            yield _sse("error", {"code": "STREAM_ERROR", "message": str(e)})
            return

        latency_ms = int((time.time() - start_time) * 1000)
        turn_seconds = estimate_turn_duration(full_response)

        if user_id:
            asyncio.create_task(
                queries.store_turn(
                    session_id=body.session_id,
                    mode=body.mode,
                    user_input=body.message,
                    response=full_response,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms,
                    rag_chunks_used=len(rag_chunks),
                )
            )
            asyncio.create_task(
                session_svc.append_turn(body.session_id, body.message, full_response)
            )

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
        await queries.update_turn_audio_seconds(body.turn_id, body.played_seconds)
    user_id = user["sub"] if user else None
    await add_quota_usage(user_id, body.session_id, body.played_seconds)


@router.post("/interrupt", status_code=204)
async def interrupt_turn(body: InterruptRequest, user: dict | None = Depends(get_current_user)):
    if user and body.active_turn_id:
        await queries.update_turn_audio_seconds(body.active_turn_id, body.played_seconds)
    user_id = user["sub"] if user else None
    await add_quota_usage(user_id, body.session_id, body.played_seconds)


@router.post("/end", status_code=204)
async def end_session(body: EndSessionRequest, user: dict | None = Depends(get_current_user)):
    if user:
        await queries.end_session(body.session_id)
    await session_svc.clear_session(body.session_id)


@router.get("/sessions")
async def conversation_history(
    limit: int = 20, offset: int = 0, user: dict = Depends(get_current_user)
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    sessions = await queries.get_user_sessions(user["sub"], limit, offset)
    return [dict(s) for s in sessions]


@router.get("/sessions/{session_id}/turns")
async def get_session_detail(session_id: str, user: dict = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    turns = await queries.get_session_turns(session_id)
    return [dict(t) for t in turns]
