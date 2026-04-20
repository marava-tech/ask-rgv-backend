import httpx
from core.config import settings

SMALLEST_URL = "https://waves-api.smallest.ai/api/v1/lightning/get_speech"

_VOICE_MAP = {"te": "smallest_ai_voice_te", "hi": "smallest_ai_voice_hi"}


async def synthesise_speech(text: str, language: str) -> bytes:
    """Call Smallest.ai Lightning V3.1. Returns raw audio bytes (mp3)."""
    voice_attr = _VOICE_MAP.get(language, "smallest_ai_voice_en")
    voice_id = getattr(settings, voice_attr) or settings.smallest_ai_voice_en

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            SMALLEST_URL,
            headers={
                "Authorization": f"Bearer {settings.smallest_ai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "voice_id": voice_id,
                "sample_rate": 24000,
                "speed": 1.0,
                "add_wav_header": True,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Smallest.ai error {response.status_code}: {response.text[:200]}")

    return response.content
