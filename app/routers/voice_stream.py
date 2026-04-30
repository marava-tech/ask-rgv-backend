import asyncio
import base64
import json
import logging
import re
import time
import uuid as _uuid

import jwt
from fastapi import APIRouter, WebSocket

from core.auth import decode_access_token
from core.config import settings
from db import queries
from services import session as session_svc
from services import stt_stream
from services.claude import haiku_call, sonnet_stream
from services.crisis import detect_crisis, get_safety_response
from services.embedding import embed_query
from services.intent import classify_intent
from services.language import get_session_language, set_session_language
from services.prompt import assemble_prompt, estimate_turn_duration
from services.qdrant_search import search_chunks
from services.metrics import log_turn
from services.quota import (
    add_quota_usage,
    get_quota_remaining,
    get_tier_limit_seconds,
    is_jwt_blacklisted,
    verify_device_token,
)
from services.style_profiles import get_style_anchors
from services.tts import synthesise_sentence

_log = logging.getLogger(__name__)

_SENT_RE = re.compile(r"(?<=[.!?।॥])\s")


def _pop_sentences(buf: str) -> tuple[list[str], str]:
    parts = _SENT_RE.split(buf)
    if len(parts) <= 1:
        return [], buf
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


async def _tts_bg(seq: int, text: str, lang: str, results: dict[int, bytes]) -> None:
    try:
        wav = await synthesise_sentence(text, lang, prepend_silence=(seq == 0))
        results[seq] = wav
    except Exception as e:
        _log.warning("[voice_stream:tts] seq=%d failed: %r", seq, e)
        results[seq] = b""


def _flush_audio(
    results: dict[int, bytes], next_seq: int
) -> tuple[list[tuple[int, bytes]], int]:
    ready: list[tuple[int, bytes]] = []
    while next_seq in results:
        ready.append((next_seq, results.pop(next_seq)))
        next_seq += 1
    return ready, next_seq


async def _generate_title(session_id: str, user_input: str) -> None:
    try:
        title = await haiku_call(
            f'Generate a concise 3-5 word title for a conversation that started with: "{user_input}". '
            "Return ONLY the title, no quotes, no punctuation at the end."
        )
        title = title.strip().strip('"').strip("'")[:80]
        if title:
            await queries.update_session_title(session_id, title)
    except Exception as e:
        _log.warning("[voice_stream] title generation failed for %s: %r", session_id, e)


async def _update_user_memory(user_id: str, user_input: str, rgv_response: str) -> None:
    """Post-turn: Haiku summarizes the exchange and upserts user_memory for Super Fan."""
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
        import json as _json
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()
        data = _json.loads(raw)
        summary = str(data.get("summary", existing_summary))[:2000]
        key_facts = data.get("key_facts", existing_facts)
        if not isinstance(key_facts, dict):
            key_facts = existing_facts
        await queries.upsert_user_memory(user_id, summary, key_facts)
    except Exception as e:
        _log.warning("[voice_stream] user_memory update failed for %s: %r", user_id, e)


async def _resolve_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        jti = payload.get("jti")
        if jti and await is_jwt_blacklisted(jti):
            return None
        return payload
    except jwt.PyJWTError:
        return None


router = APIRouter(prefix="/voice", tags=["voice"])


@router.websocket("/stream")
async def voice_stream(
    ws: WebSocket,
    session_id: str,
    mode: str = "default",
    device_id: str | None = None,
    device_token: str | None = None,
    token: str | None = None,
    hint_language: str | None = None,  # validated below against known codes
) -> None:
    """
    Unified bidirectional voice WebSocket.

    Client → Server: binary frames, raw 16-bit PCM, 16 kHz mono.
    Client → Server: text JSON {"type": "end_audio"} to manually stop recording.
    Server → Client: JSON events:
      transcript_interim, transcript_final, meta, safety, token,
      audio, turn_id, quota_exhausted, done, error
    """
    await ws.accept()

    # Reject bogus hint_language values before any audio is read.
    if hint_language is not None and hint_language not in {"te", "hi", "en"}:
        hint_language = None

    # Flutter sends auth token as first text frame ({"type":"auth","token":"..."})
    # to avoid it appearing in server/proxy logs. Peek at the first message if
    # no token was passed as a query param.
    _peeked_audio: bytes | None = None
    if not token:
        try:
            first = await asyncio.wait_for(ws.receive(), timeout=3.0)
            if first.get("type") == "websocket.receive":
                text = first.get("text")
                if text:
                    ctrl = json.loads(text)
                    if ctrl.get("type") == "auth":
                        token = ctrl.get("token")
                else:
                    # Anonymous user — first frame is already audio; save it
                    _peeked_audio = first.get("bytes")
        except (asyncio.TimeoutError, Exception):
            pass

    user = await _resolve_user(token)
    user_id = user["sub"] if user else None

    # Resolve device_id: prefer server-signed device_token over raw device_id.
    resolved_device_id = device_id
    if not user_id:
        if device_token:
            verified = verify_device_token(device_token)
            if verified:
                resolved_device_id = verified
            else:
                _log.warning("[voice_stream][security] invalid device_token — closing ws")
                await ws.send_json({"type": "error", "message": "invalid_device_token"})
                await ws.close()
                return
        elif not device_id:
            await ws.send_json({"type": "error", "message": "device_token required for anonymous sessions"})
            await ws.close()
            return

    if user_id:
        user_data, lang = await asyncio.gather(
            queries.get_user_by_id(user_id),
            get_session_language(session_id),
        )
        tier = (user_data["tier"] if user_data else "free")
    else:
        lang = await get_session_language(session_id)
        user_data = None
        tier = "anonymous"

    quota_remaining = await get_quota_remaining(user_id, resolved_device_id, tier)
    if quota_remaining == 0:
        await ws.send_json({"type": "quota_exhausted", "tier": tier})
        await ws.close()
        return

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=200)
    loop = asyncio.get_event_loop()
    utterance_future: asyncio.Future = loop.create_future()

    # For speculative RAG: start embed on first final transcript so it's cached
    # when search_chunks runs after UtteranceEnd.
    speculative_embed_task: asyncio.Task | None = None
    speculative_text = ""

    async def _send(msg: dict) -> None:
        try:
            await ws.send_json(msg)
        except Exception:
            pass

    async def on_interim(text: str, language: str) -> None:
        await _send({"type": "transcript_interim", "text": text, "language": language})

    async def on_final(text: str, language: str) -> None:
        nonlocal speculative_embed_task, speculative_text
        await _send({"type": "transcript_final", "text": text, "language": language})
        if settings.speculative_rag_enabled and text != speculative_text:
            speculative_text = text
            speculative_embed_task = asyncio.create_task(embed_query(text))

    async def on_utterance_end(text: str, language: str) -> None:
        if not utterance_future.done():
            utterance_future.set_result((text, language))

    async def _read_client() -> None:
        if _peeked_audio:
            await audio_queue.put(_peeked_audio)
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg["type"] == "websocket.receive":
                    data = msg.get("bytes")
                    if data:
                        await audio_queue.put(data)
                    else:
                        text = msg.get("text")
                        if text:
                            try:
                                ctrl = json.loads(text)
                                if ctrl.get("type") == "end_audio":
                                    break
                            except Exception:
                                pass
        except Exception:
            pass
        finally:
            await audio_queue.put(None)

    # Server-side preferred_language fallback: if hint_language not supplied by client,
    # use the authenticated user's stored preference before falling back to multi.
    effective_hint = hint_language or (user_data and user_data.get("preferred_language"))

    read_task = asyncio.create_task(_read_client())
    dg_task = asyncio.create_task(
        stt_stream.run_deepgram_stream(audio_queue, on_interim, on_final, on_utterance_end, language=lang or effective_hint)
    )

    try:
        transcript, detected_lang = await asyncio.wait_for(utterance_future, timeout=30.0)
    except asyncio.TimeoutError:
        await _send({"type": "error", "code": "NO_SPEECH_DETECTED"})
        read_task.cancel()
        dg_task.cancel()
        await ws.close()
        return

    # Signal end of audio; let Deepgram task finish — always put sentinel, never skip
    await audio_queue.put(None)
    read_task.cancel()

    if not transcript:
        await _send({"type": "error", "code": "NO_TRANSCRIPT"})
        await ws.close()
        await dg_task
        return

    # Language resolution: prefer caller-supplied hint (mirrors /turn behaviour)
    if lang is None:
        lang = hint_language or detected_lang
        asyncio.create_task(set_session_language(session_id, lang))
        if user_id:
            asyncio.create_task(queries.update_session_language(session_id, lang))
    await _send({"type": "meta", "language": lang})

    # Crisis check — always before LLM
    is_crisis, trigger = detect_crisis(transcript)
    if is_crisis:
        asyncio.create_task(queries.log_crisis_event(session_id, trigger))
        await _send({"type": "safety", "text": get_safety_response(lang)})
        await _send({"type": "done"})
        await ws.close()
        await dg_task
        return

    # RAG + LLM + TTS — speculative embed may have already warmed the cache
    history, rag_chunks, style_anchors, intent = await asyncio.gather(
        session_svc.get_history(session_id),
        search_chunks(transcript),
        get_style_anchors(lang),
        classify_intent(transcript),
    )
    is_first_turn = len(history) == 0

    # Load persistent memory for Super Fan users
    user_memories_text: str | None = None
    if tier == "super_fan" and user_id:
        mem = await queries.get_user_memory(user_id)
        if mem and mem.get("summary"):
            user_memories_text = mem["summary"]

    messages, system_blocks = assemble_prompt(
        intent=intent,
        history=history,
        rag_chunks=rag_chunks,
        style_anchors=style_anchors,
        language=lang,
        user_input=transcript,
        mode=mode,
        user_name=user_data.get("preferred_name") if user_data else None,
        user_memories=user_memories_text,
    )

    preallocated_turn_id = str(_uuid.uuid4()) if user_id else None
    t_request = time.time()
    t_first_token: float | None = None
    t_first_audio: float | None = None
    full_response = ""
    usage_out: dict = {}

    sentence_buf = ""
    audio_results: dict[int, bytes] = {}
    tts_tasks: list[asyncio.Task] = []
    tts_seq = 0
    next_emit = 0
    pipeline_error: Exception | None = None

    try:
        async for token_text in sonnet_stream(messages, system_blocks, usage_out):
            if t_first_token is None:
                t_first_token = time.time()
            full_response += token_text
            await _send({"type": "token", "text": token_text})

            sentence_buf += token_text
            sentences, sentence_buf = _pop_sentences(sentence_buf)
            for sentence in sentences:
                t = asyncio.create_task(_tts_bg(tts_seq, sentence, lang, audio_results))
                tts_tasks.append(t)
                tts_seq += 1

            ready, next_emit = _flush_audio(audio_results, next_emit)
            for seq, wav in ready:
                if wav:
                    if t_first_audio is None:
                        t_first_audio = time.time()
                    await _send({
                        "type": "audio",
                        "seq": seq,
                        "b64": base64.b64encode(wav).decode(),
                        "mime": "audio/wav",
                    })

        # Flush tail sentence
        if sentence_buf.strip():
            t = asyncio.create_task(_tts_bg(tts_seq, sentence_buf.strip(), lang, audio_results))
            tts_tasks.append(t)
            tts_seq += 1

        if tts_tasks:
            await asyncio.gather(*tts_tasks, return_exceptions=True)
            ready, next_emit = _flush_audio(audio_results, next_emit)
            for seq, wav in ready:
                if wav:
                    if t_first_audio is None:
                        t_first_audio = time.time()
                    await _send({
                        "type": "audio",
                        "seq": seq,
                        "b64": base64.b64encode(wav).decode(),
                        "mime": "audio/wav",
                    })

    except Exception as e:
        pipeline_error = e
        # Cancel any remaining TTS tasks on failure
        for t in tts_tasks:
            t.cancel()
        if tts_tasks:
            await asyncio.gather(*tts_tasks, return_exceptions=True)

    finally:
        # Always charge quota for generated content, success or partial failure
        if full_response:
            turn_seconds = estimate_turn_duration(full_response)
            limit = await get_tier_limit_seconds(tier)
            new_used = await add_quota_usage(user_id, resolved_device_id, turn_seconds, limit)
            if limit != -1 and new_used >= limit:
                await _send({"type": "quota_exhausted", "tier": tier})

    if pipeline_error:
        _log.exception("[voice_stream] pipeline error: %r", pipeline_error)
        await _send({"type": "error", "code": "PIPELINE_ERROR"})
        await ws.close()
        await dg_task
        return

    latency_ms = int((time.time() - t_request) * 1000)
    ttft_ms = int((t_first_token - t_request) * 1000) if t_first_token else None
    first_audio_ms = int((t_first_audio - t_request) * 1000) if t_first_audio else None
    turn_seconds = estimate_turn_duration(full_response)
    tokens_used = usage_out.get("input_tokens", 0) + usage_out.get("output_tokens", 0)

    # Append to Redis history (fast, awaited)
    await session_svc.append_turn(session_id, transcript, full_response)

    if user_id:
        await _send({"type": "turn_id", "id": preallocated_turn_id})
        asyncio.create_task(queries.store_turn(
            session_id=session_id,
            mode=mode,
            user_input=transcript,
            response=full_response,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            rag_chunks_used=len(rag_chunks),
            turn_id=preallocated_turn_id,
        ))
        if is_first_turn:
            asyncio.create_task(_generate_title(session_id, transcript))
        if tier == "super_fan":
            asyncio.create_task(_update_user_memory(user_id, transcript, full_response))

    log_turn(
        source="ws",
        session_id=session_id,
        user_id=user_id,
        tier=tier,
        lang=lang,
        ttft_ms=ttft_ms,
        first_audio_ms=first_audio_ms,
        latency_ms=latency_ms,
        rag_chunks=len(rag_chunks),
        turn_seconds=turn_seconds,
    )

    await _send({
        "type": "done",
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
        "first_audio_ms": first_audio_ms,
        "rag_chunks": len(rag_chunks),
        "turn_seconds": turn_seconds,
    })

    await ws.close(code=1000)
    await dg_task
