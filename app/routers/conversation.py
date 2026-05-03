import asyncio
import base64
import json
import logging
import time
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
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
from services.claude import sonnet_stream
from services.crisis import detect_crisis, get_safety_response
from services.intent import classify_intent
from services.language import detect_language, get_session_language, set_session_language
from services.quota import get_redis as _get_redis
from services.prompt import assemble_prompt, estimate_turn_duration
from services.qdrant_search import search_chunks
from services.quota import add_quota_usage, get_quota_remaining, get_tier_limit_seconds, verify_device_token
from services.session import generate_title
from services.style_profiles import get_style_anchors
from services.metrics import log_turn
from services.tts import flush_audio, pop_sentences, tts_bg

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/conversation", tags=["conversation"])

_ASSESSMENT_CACHE_TTL = 3600  # 1 hour per session


async def _get_session_assessment(session_id: str, user_id: str | None) -> str | None:
    if not user_id:
        return None
    r = _get_redis()
    cache_key = f"session:assessment:{session_id}"
    cached = await r.get(cache_key)
    if cached is not None:
        return cached or None  # empty string means "no assessment" sentinel
    row = await queries.get_user_valid_assessment(user_id)
    if row:
        assessment_row = await queries.get_latest_assessment(user_id)
        text = (assessment_row or {}).get("assessment_text") or ""
        await r.setex(cache_key, _ASSESSMENT_CACHE_TTL, text)
        return text or None
    await r.setex(cache_key, _ASSESSMENT_CACHE_TTL, "")
    return None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _resolve_device_id(device_token: str | None, device_id: str | None) -> tuple[str | None, bool]:
    """Return (resolved_device_id, is_verified). Prefers signed token over raw id."""
    if device_token:
        verified = verify_device_token(device_token)
        if verified:
            return verified, True
        _log.warning("[security] invalid device_token presented — rejecting anonymous request")
        return None, False
    if device_id:
        _log.warning("[security] unverified device_id used (old client) — consider upgrading")
        return device_id, False
    return None, False


@router.post("/start", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None
    session_id = await queries.create_session(user_id, body.device_id)
    return StartSessionResponse(session=SessionData(
        id=session_id,
        mode=body.mode,
        started_at=datetime.now(timezone.utc).isoformat(),
    ))


@router.post("/turn")
async def conversation_turn(request: Request, body: TurnRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None

    device_id: str | None = None
    if not user_id:
        device_id, _ = _resolve_device_id(body.device_token, body.device_id)
        if not device_id:
            raise HTTPException(status_code=400, detail="device_token required for anonymous sessions")

    async def event_stream():
        try:
            # Parallelize user-data fetch + language Redis lookup — they share no dependencies.
            user_coro = queries.get_user_by_id(user_id) if user_id else asyncio.sleep(0)
            user_data, lang = await asyncio.gather(
                user_coro,
                get_session_language(body.session_id),
            )
            tier = (user_data["tier"] if user_data else "free") if user_id else "anonymous"

            quota_remaining = await get_quota_remaining(user_id, device_id, tier)
            if quota_remaining == 0:
                yield _sse("quota_exhausted", {"tier": tier})
                return

            # Finish language resolution if Redis was a miss.
            if lang is None:
                if user_id:
                    lang = await queries.get_session_language_from_db(body.session_id)
                    if lang:
                        asyncio.create_task(set_session_language(body.session_id, lang))
                if lang is None:
                    if body.hint_language and body.source == "voice":
                        lang = body.hint_language
                    elif user_data and user_data.get("preferred_language"):
                        lang = user_data["preferred_language"]
                    else:
                        lang = detect_language(body.message)
                    asyncio.create_task(set_session_language(body.session_id, lang))
                    if user_id:
                        asyncio.create_task(queries.update_session_language(body.session_id, lang))

            yield _sse("meta", {"language": lang})

            is_crisis, trigger = detect_crisis(body.message)
            if is_crisis:
                asyncio.create_task(queries.log_crisis_event(body.session_id, trigger))
                yield _sse("safety", {"text": get_safety_response(lang)})
                yield _sse("done", {})
                return

            history, rag_chunks, style_anchors, intent = await asyncio.gather(
                session_svc.get_history(body.session_id),
                search_chunks(body.message),
                get_style_anchors(lang),
                classify_intent(body.message),
            )

            rag_context = [
                {
                    "chunk_id": str(c["id"]),
                    "video_id": c["payload"].get("video_id"),
                    "text_snippet": c["payload"].get("text", "")[:300],
                    "qdrant_score": round(float(c["score"]), 4),
                }
                for c in rag_chunks[:3]
            ] or None

            is_first_turn = (len(history) == 0)

            assessment_text = await _get_session_assessment(body.session_id, user_id)

            messages, system_blocks = await assemble_prompt(
                intent=intent,
                history=history,
                rag_chunks=rag_chunks,
                style_anchors=style_anchors,
                language=lang,
                user_input=body.message,
                mode=body.mode,
                user_name=user_data.get("preferred_name") if user_data else None,
                loader=getattr(request.app.state, "prompt_loader", None),
                assessment_text=assessment_text,
            )

            # Pre-allocate turn_id so we can return it immediately after streaming,
            # without waiting for the Postgres INSERT.
            preallocated_turn_id = str(_uuid.uuid4()) if user_id else None

            t_request = time.time()
            t_first_token: float | None = None
            t_first_audio: float | None = None
            full_response = ""
            usage_out: dict = {}

            # Sentence-streaming TTS state
            sentence_buf = ""
            audio_results: dict[int, bytes] = {}
            tts_tasks: list[asyncio.Task] = []
            tts_seq = 0
            next_emit = 0

            stream_error: Exception | None = None
            for attempt in range(2):
                full_response = ""
                usage_out = {}
                sentence_buf = ""
                audio_results.clear()
                tts_tasks.clear()
                tts_seq = 0
                next_emit = 0
                try:
                    async for token in sonnet_stream(messages, system_blocks, usage_out):
                        if t_first_token is None:
                            t_first_token = time.time()
                        full_response += token
                        yield _sse("token", {"text": token})

                        sentence_buf += token
                        sentences, sentence_buf = pop_sentences(sentence_buf)
                        for sentence in sentences:
                            t = asyncio.create_task(tts_bg(tts_seq, sentence, lang, audio_results))
                            tts_tasks.append(t)
                            tts_seq += 1

                        ready, next_emit = flush_audio(audio_results, next_emit)
                        for seq, wav in ready:
                            if wav:
                                if t_first_audio is None:
                                    t_first_audio = time.time()
                                yield _sse("audio_chunk", {
                                    "seq": seq,
                                    "b64": base64.b64encode(wav).decode(),
                                    "mime": "audio/wav",
                                })

                    stream_error = None
                    break
                except Exception as e:
                    stream_error = e
                    if full_response:
                        break
                    _log.warning("[turn] stream attempt %d failed: %r — retrying", attempt + 1, e)
                    await asyncio.sleep(0.3)

            if stream_error:
                for t in tts_tasks:
                    t.cancel()
                if tts_tasks:
                    await asyncio.gather(*tts_tasks, return_exceptions=True)
                _log.exception("[turn] stream error after retries: %r", stream_error)
                if full_response and user_id:
                    await session_svc.append_turn(body.session_id, body.message, full_response)
                yield _sse("error", {"code": "STREAM_ERROR"})
                return

            if sentence_buf.strip():
                t = asyncio.create_task(tts_bg(tts_seq, sentence_buf.strip(), lang, audio_results))
                tts_tasks.append(t)
                tts_seq += 1

            if tts_tasks:
                await asyncio.gather(*tts_tasks, return_exceptions=True)
                ready, next_emit = flush_audio(audio_results, next_emit)
                for seq, wav in ready:
                    if wav:
                        if t_first_audio is None:
                            t_first_audio = time.time()
                        yield _sse("audio_chunk", {
                            "seq": seq,
                            "b64": base64.b64encode(wav).decode(),
                            "mime": "audio/wav",
                        })

            latency_ms = int((time.time() - t_request) * 1000)
            ttft_ms = int((t_first_token - t_request) * 1000) if t_first_token else None
            first_audio_ms = int((t_first_audio - t_request) * 1000) if t_first_audio else None
            turn_seconds = estimate_turn_duration(full_response)
            tokens_used = usage_out.get("input_tokens", 0) + usage_out.get("output_tokens", 0)

            await session_svc.append_turn(body.session_id, body.message, full_response)

            if user_id:
                yield _sse("turn_id", {"id": preallocated_turn_id})
                asyncio.create_task(queries.store_turn(
                    session_id=body.session_id,
                    mode=body.mode,
                    user_input=body.message,
                    response=full_response,
                    tokens_used=tokens_used,
                    latency_ms=latency_ms,
                    rag_chunks_used=len(rag_chunks),
                    turn_id=preallocated_turn_id,
                    rag_context=rag_context,
                ))
                if is_first_turn:
                    asyncio.create_task(generate_title(body.session_id, body.message, lang))

            # C-1: voice turns charged by /voice/tts; only charge here for text-only turns.
            if body.source == "text":
                limit = await get_tier_limit_seconds(tier)
                new_used = await add_quota_usage(user_id, device_id, turn_seconds, limit)
                if limit != -1 and new_used >= limit:
                    yield _sse("quota_exhausted", {"tier": tier, "upgrade_url": "/subscription"})

            log_turn(
                source="sse",
                session_id=body.session_id,
                user_id=user_id,
                tier=tier,
                lang=lang,
                ttft_ms=ttft_ms,
                first_audio_ms=first_audio_ms,
                latency_ms=latency_ms,
                rag_chunks=len(rag_chunks),
                turn_seconds=turn_seconds,
            )

            yield _sse("done", {
                "latency_ms": latency_ms,
                "ttft_ms": ttft_ms,
                "first_audio_ms": first_audio_ms,
                "rag_chunks": len(rag_chunks),
                "turn_seconds": turn_seconds,
            })

            if user_id:
                conv_count = await queries.get_user_conversation_count(user_id)
                if conv_count == 4:
                    valid_assessment = await queries.get_user_valid_assessment(user_id)
                    if not valid_assessment:
                        yield _sse("quiz_prompt", {"message": "RGV wants to assess you."})
        except Exception as e:
            _log.exception("[turn] setup error: %r", e)
            yield _sse("error", {"code": "STREAM_ERROR"})
            return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/usage", status_code=204)
async def record_usage(body: UsageRequest, user: dict | None = Depends(get_current_user)):
    if user and body.turn_id:
        await queries.update_turn_audio_seconds(body.turn_id, body.played_seconds, user["sub"])
    user_id = user["sub"] if user else None
    if user_id:
        user_data = await queries.get_user_by_id(user_id)
        tier = user_data["tier"] if user_data else "free"
    else:
        tier = "anonymous"
    limit = await get_tier_limit_seconds(tier)
    await add_quota_usage(user_id, body.device_id, body.played_seconds, limit)


@router.post("/interrupt", status_code=204)
async def interrupt_turn(body: InterruptRequest, user: dict | None = Depends(get_current_user)):
    if user and body.active_turn_id:
        await queries.update_turn_audio_seconds(body.active_turn_id, body.played_seconds, user["sub"])
    user_id = user["sub"] if user else None
    if user_id:
        user_data = await queries.get_user_by_id(user_id)
        tier = user_data["tier"] if user_data else "free"
    else:
        tier = "anonymous"
    limit = await get_tier_limit_seconds(tier)
    await add_quota_usage(user_id, body.device_id, body.played_seconds, limit)


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
    limit = max(1, min(100, limit))
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
async def get_session_detail(
    session_id: str,
    limit: int = 200,
    before: str | None = None,
    user: dict = Depends(get_current_user),
):
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    limit = max(1, min(500, limit))
    before_dt = datetime.fromisoformat(before) if before else None
    session, turns = await asyncio.gather(
        queries.get_session(session_id),
        queries.get_session_turns(session_id, limit=limit, before=before_dt),
    )
    if not session or (session["user_id"] and str(session["user_id"]) != user["sub"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return turns
