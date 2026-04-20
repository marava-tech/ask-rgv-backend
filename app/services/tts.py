import re
import httpx
from core.config import settings

SMALLEST_URL = "https://waves-api.smallest.ai/api/v1/lightning/get_speech"
_TTS_CHAR_LIMIT = 250

_VOICE_MAP = {"te": "smallest_ai_voice_te", "hi": "smallest_ai_voice_hi"}


def _truncate_to_limit(text: str, limit: int = _TTS_CHAR_LIMIT) -> str:
    """Truncate at the last sentence boundary that fits within limit."""
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    # Find last sentence-ending punctuation
    match = re.search(r'[.!?][^.!?]*$', chunk)
    if match:
        return chunk[:match.start() + 1].strip()
    # Fallback: last word boundary
    last_space = chunk.rfind(' ')
    return chunk[:last_space].strip() if last_space != -1 else chunk


async def synthesise_speech(text: str, language: str) -> bytes:
    """Call Smallest.ai Lightning V3.1. Returns WAV bytes."""
    voice_attr = _VOICE_MAP.get(language, "smallest_ai_voice_en")
    voice_id = getattr(settings, voice_attr) or settings.smallest_ai_voice_en

    tts_text = _truncate_to_limit(text)

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            SMALLEST_URL,
            headers={
                "Authorization": f"Bearer {settings.smallest_ai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "text": tts_text,
                "voice_id": voice_id,
                "sample_rate": 24000,
                "speed": 1.0,
                "add_wav_header": True,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Smallest.ai error {response.status_code}: {response.text[:200]}")

    return response.content
