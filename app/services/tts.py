import io
import re
import wave
import asyncio
import httpx
from core.config import settings

SMALLEST_URL = "https://waves-api.smallest.ai/api/v1/lightning/get_speech"
_TTS_CHAR_LIMIT = 250

_VOICE_MAP = {"te": "smallest_ai_voice_te", "hi": "smallest_ai_voice_hi"}


def _split_chunks(text: str, limit: int = _TTS_CHAR_LIMIT) -> list[str]:
    """Split text into sentence-boundary chunks each within limit chars."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    chunks, current = [], ""
    for sentence in sentences:
        # Single sentence too long — hard-split at word boundary
        while len(sentence) > limit:
            space = sentence.rfind(' ', 0, limit)
            cut = space if space != -1 else limit
            chunks.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if len(current) + len(sentence) + 1 <= limit:
            current = (current + " " + sentence).strip() if current else sentence
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _concat_wavs(wav_chunks: list[bytes]) -> bytes:
    """Merge multiple WAV byte blobs into one, preserving the first header."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        for i, chunk in enumerate(wav_chunks):
            with wave.open(io.BytesIO(chunk)) as w:
                if i == 0:
                    out.setparams(w.getparams())
                out.writeframes(w.readframes(w.getnframes()))
    return buf.getvalue()


_SEMAPHORE = asyncio.Semaphore(5)


async def _synthesise_chunk(client: httpx.AsyncClient, text: str, voice_id: str) -> bytes:
    async with _SEMAPHORE:
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


async def synthesise_speech(text: str, language: str) -> bytes:
    """Split text into ≤250-char chunks, call Smallest.ai in parallel, return merged WAV."""
    voice_attr = _VOICE_MAP.get(language, "smallest_ai_voice_en")
    voice_id = getattr(settings, voice_attr) or settings.smallest_ai_voice_en

    chunks = _split_chunks(text)

    async with httpx.AsyncClient(timeout=30.0) as client:
        wav_chunks = await asyncio.gather(
            *[_synthesise_chunk(client, chunk, voice_id) for chunk in chunks]
        )

    if len(wav_chunks) == 1:
        return wav_chunks[0]
    return _concat_wavs(list(wav_chunks))
