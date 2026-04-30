import asyncio
import base64
import json
import logging
import re
import time
import uuid as _uuid
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
from services.crisis import detect_crisis, get_safety_response
from services.intent import classify_intent
from services.language import detect_language, get_session_language, set_session_language
from services.prompt import assemble_prompt, estimate_turn_duration
from services.qdrant_search import search_chunks
from services.quota import add_quota_usage, get_quota_remaining, get_tier_limit_seconds, verify_device_token
from services.style_profiles import get_style_anchors
from services.metrics import log_turn
from services.tts import synthesise_sentence

_log = logging.getLogger(__name__)

# Sentence boundary: split after [.!?।॥] followed by whitespace.
# Lookbehind keeps the terminator with the preceding sentence.
_SENT_RE = re.compile(r'(?<=[.!?।॥])\s')


def _pop_sentences(buf: str) -> tuple[list[str], str]:
    """Split buf on sentence boundaries; return (complete sentences, remaining tail)."""
    parts = _SENT_RE.split(buf)
    if len(parts) <= 1:
        return [], buf
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


async def _tts_bg(seq: int, text: str, lang: str, results: dict[int, bytes]) -> None:
    try:
        wav = await synthesise_sentence(text, lang)
        results[seq] = wav
    except Exception as e:
        _log.warning("[tts_bg] seq=%d failed: %r", seq, e)
        results[seq] = b""


def _flush_audio(results: dict[int, bytes], next_seq: int) -> tuple[list[tuple[int, bytes]], int]:
    """Drain audio_results in monotonic order, returning ready (seq, wav) pairs."""
    ready: list[tuple[int, bytes]] = []
    while next_seq in results:
        ready.append((next_seq, results.pop(next_seq)))
        next_seq += 1
    return ready, next_seq


router = APIRouter(prefix="/conversation", tags=["conversation"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


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


@router.post("/start", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest, user: dict | None = Depends(get_current_user)):
    user_id = user["sub"] if user else None
    session_id = await queries.create_session(user_id, body.device_id)
    return StartSessionResponse(session=SessionData(
        id=session_id,
        mode=body.mode,
        started_at=datetime.now(timezone.utc).isoformat(),
    ))


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


@router.post("/turn")
async def conversation_turn(body: TurnRequest, user: dict | None = Depends(get_current_user)):
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

            is_first_turn = (len(history) == 0)

            messages, system_blocks = assemble_prompt(
                intent=intent,
                history=history,
                rag_chunks=rag_chunks,
                style_anchors=style_anchors,
                language=lang,
                user_input=body.message,
                mode=body.mode,
                user_name=user_data.get("preferred_name") if user_data else None,
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

                        # Accumulate into sentence buffer; fire TTS when sentence completes.
                        sentence_buf += token
                        sentences, sentence_buf = _pop_sentences(sentence_buf)
                        for sentence in sentences:
                            t = asyncio.create_task(_tts_bg(tts_seq, sentence, lang, audio_results))
                            tts_tasks.append(t)
                            tts_seq += 1

                        # Non-blocking drain: emit any TTS chunks that are already ready.
                        ready, next_emit = _flush_audio(audio_results, next_emit)
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
                # Cancel any in-flight TTS tasks to avoid leaking Smallest.ai calls
                for t in tts_tasks:
                    t.cancel()
                if tts_tasks:
                    await asyncio.gather(*tts_tasks, return_exceptions=True)
                _log.exception("[turn] stream error after retries: %r", stream_error)
                if full_response and user_id:
                    await session_svc.append_turn(body.session_id, body.message, full_response)
                yield _sse("error", {"code": "STREAM_ERROR"})
                return

            # Flush any remaining text as one last TTS chunk.
            if sentence_buf.strip():
                t = asyncio.create_task(_tts_bg(tts_seq, sentence_buf.strip(), lang, audio_results))
                tts_tasks.append(t)
                tts_seq += 1

            # Wait for all in-flight TTS tasks, then emit remaining chunks in order.
            if tts_tasks:
                await asyncio.gather(*tts_tasks, return_exceptions=True)
                ready, next_emit = _flush_audio(audio_results, next_emit)
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

            # Append to Redis history (fast, keep awaited).
            await session_svc.append_turn(body.session_id, body.message, full_response)

            if user_id:
                # Return pre-allocated turn_id immediately — PG write is fire-and-forget.
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
                ))
                if is_first_turn:
                    asyncio.create_task(_generate_title(body.session_id, body.message))

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
