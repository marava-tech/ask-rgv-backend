import httpx
from core.config import settings

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_PARAMS = {
    "model": "nova-2",
    "detect_language": "true",
    "punctuate": "true",
    "smart_format": "true",
}


async def transcribe_audio(audio_bytes: bytes, content_type: str) -> dict:
    """Send audio to Deepgram Nova-2. Returns {transcript, language, duration_seconds}."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            DEEPGRAM_URL,
            params=DEEPGRAM_PARAMS,
            headers={
                "Authorization": f"Token {settings.deepgram_api_key}",
                "Content-Type": content_type,
            },
            content=audio_bytes,
        )
        response.raise_for_status()

    data = response.json()
    channel = data["results"]["channels"][0]
    alternative = channel["alternatives"][0]

    transcript = alternative.get("transcript", "").strip()
    detected_lang = channel.get("detected_language", "en")
    duration = data["metadata"].get("duration", 0.0)

    lang = _normalise_lang(detected_lang)
    return {"transcript": transcript, "language": lang, "duration_seconds": round(duration, 2)}


def _normalise_lang(deepgram_lang: str) -> str:
    code = deepgram_lang.lower().split("-")[0]
    if code == "te":
        return "te"
    if code == "hi":
        return "hi"
    return "en"
