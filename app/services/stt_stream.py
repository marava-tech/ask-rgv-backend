import asyncio
import json
import logging
from typing import Awaitable, Callable

from core.config import settings

_log = logging.getLogger(__name__)

_WS_URL_BASE = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-2"
    "&interim_results=true"
    "&endpointing=300"
    "&utterance_end_ms=1000"
    "&vad_events=true"
    "&encoding=linear16"
    "&sample_rate=16000"
    "&channels=1"
    "&punctuate=true"
    "&smart_format=true"
)

# detect_language is NOT supported in streaming mode (pre-recorded only) — use explicit language param instead.
_LANG_MAP = {"te": "te", "hi": "hi", "en": "en-US"}


def _build_ws_url(language: str | None) -> str:
    lang_param = _LANG_MAP.get(language or "en", "en-US")
    return f"{_WS_URL_BASE}&language={lang_param}"


def _normalise_lang(code: str) -> str:
    c = code.lower().split("-")[0]
    if c == "te":
        return "te"
    if c == "hi":
        return "hi"
    return "en"


async def run_deepgram_stream(
    audio_queue: asyncio.Queue,
    on_interim: Callable[[str, str], Awaitable[None]],
    on_final: Callable[[str, str], Awaitable[None]],
    on_utterance_end: Callable[[str, str], Awaitable[None]],
    language: str | None = None,
) -> None:
    """
    Open a Deepgram WebSocket, forward PCM frames from audio_queue, fire async callbacks.

    audio_queue items:
      bytes → 16-bit PCM chunk (16 kHz mono) to forward
      None  → signals end; sends CloseStream and exits

    Callbacks (all async):
      on_interim(text, language)       — non-final transcript update
      on_final(text, language)         — is_final or speech_final result
      on_utterance_end(text, language) — VAD endpointing triggered

    language: session language hint ("en", "te", "hi") — used to set Deepgram language param.
              detect_language is NOT supported in streaming mode.
    """
    import websockets

    if not settings.deepgram_api_key:
        _log.error("[stt_stream] DEEPGRAM_API_KEY not configured — STT disabled")
        return

    headers = {"Authorization": f"Token {settings.deepgram_api_key}"}
    last_transcript = ""
    last_lang = language or "en"
    ws_url = _build_ws_url(language)

    try:
        async with websockets.connect(ws_url, additional_headers=headers) as ws:

            async def _send() -> None:
                while True:
                    chunk = await audio_queue.get()
                    if chunk is None:
                        try:
                            await ws.send(json.dumps({"type": "CloseStream"}))
                        except Exception:
                            pass
                        break
                    if chunk:
                        await ws.send(chunk)

            async def _recv() -> None:
                nonlocal last_transcript, last_lang
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    try:
                        ev = json.loads(raw)
                    except Exception:
                        continue

                    ev_type = ev.get("type")

                    if ev_type == "Results":
                        ch = ev.get("channel", {})
                        alts = ch.get("alternatives", [])
                        if not alts:
                            continue
                        text = alts[0].get("transcript", "").strip()
                        if not text:
                            continue
                        lang = _normalise_lang(ch.get("detected_language") or last_lang)
                        is_final = ev.get("is_final", False)
                        speech_final = ev.get("speech_final", False)
                        last_transcript = text
                        last_lang = lang
                        if is_final or speech_final:
                            await on_final(text, lang)
                            if speech_final:
                                # speech_final marks end of speech segment — resolve immediately
                                # without waiting for UtteranceEnd (which requires extra silence)
                                await on_utterance_end(text, lang)
                        else:
                            await on_interim(text, lang)

                    elif ev_type == "UtteranceEnd":
                        await on_utterance_end(last_transcript, last_lang)

                    elif ev_type == "Error":
                        _log.error("[deepgram] error: %s", ev.get("description", "unknown"))

            send_t = asyncio.create_task(_send())
            recv_t = asyncio.create_task(_recv())
            try:
                await asyncio.gather(send_t, recv_t)
            except Exception as e:
                _log.warning("[stt_stream] gather error: %r", e)
            finally:
                send_t.cancel()
                recv_t.cancel()

    except Exception as e:
        _log.error("[stt_stream] Deepgram connection error: %r", e)
