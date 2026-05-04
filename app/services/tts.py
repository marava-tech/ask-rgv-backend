import asyncio
import io
import logging
import re
import wave

import httpx

from core.config import settings

_log = logging.getLogger(__name__)

SMALLEST_URL = "https://api.smallest.ai/waves/v1/lightning-v3.1/get_speech"
_TTS_CHAR_LIMIT = 250

_VOICE_MAP = {
    "en": "voice_NWOz3M9u8X",
    "te": "voice_Pf6rxIAabq",
    "hi": "voice_0X99jM0G3H",
}

_http_client = httpx.AsyncClient(
    timeout=30.0,
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)

_SEMAPHORE = asyncio.Semaphore(5)

# ── Sentence-streaming helpers (shared by conversation and voice_stream routers) ──

SENT_RE = re.compile(r"(?<=[.!?।॥])\s")


def pop_sentences(buf: str) -> tuple[list[str], str]:
    """Split buf on sentence boundaries; return (complete sentences, remaining tail)."""
    parts = SENT_RE.split(buf)
    if len(parts) <= 1:
        return [], buf
    return [p.strip() for p in parts[:-1] if p.strip()], parts[-1]


def flush_audio(
    results: dict[int, bytes], next_seq: int
) -> tuple[list[tuple[int, bytes]], int]:
    """Drain audio_results in monotonic order, returning ready (seq, wav) pairs."""
    ready: list[tuple[int, bytes]] = []
    while next_seq in results:
        ready.append((next_seq, results.pop(next_seq)))
        next_seq += 1
    return ready, next_seq


async def tts_bg(
    seq: int,
    text: str,
    lang: str,
    results: dict[int, bytes],
    prepend_silence: bool = False,
) -> bool:
    """Returns True on success, False when both attempts fail (retry-exhausted)."""
    voice_id = _resolve_voice(lang)
    text_len = len(text)
    try:
        wav = await synthesise_sentence(text, lang, prepend_silence=prepend_silence)
        results[seq] = wav
        return True
    except Exception as e:
        _log.error("[TTS_FAILED] seq=%d voice_id=%s text_len=%d attempt=1: %r — retrying in 500ms", seq, voice_id, text_len, e)
    await asyncio.sleep(0.5)
    try:
        wav = await synthesise_sentence(text, lang, prepend_silence=prepend_silence)
        results[seq] = wav
        return True
    except Exception as e:
        _log.error("[TTS_FAILED] seq=%d voice_id=%s text_len=%d attempt=2: %r", seq, voice_id, text_len, e)
        results[seq] = b""
        return False


# ── Internal synthesis helpers ────────────────────────────────────────────────


def _split_chunks(text: str, limit: int = _TTS_CHAR_LIMIT) -> list[str]:
    """Split text into sentence-boundary chunks each within limit chars."""
    sentences = re.split(r'(?<=[.!?।॥])\s+', text.strip())
    chunks, current = [], ""
    for sentence in sentences:
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
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        for i, chunk in enumerate(wav_chunks):
            with wave.open(io.BytesIO(chunk)) as w:
                if i == 0:
                    out.setparams(w.getparams())
                out.writeframes(w.readframes(w.getnframes()))
    return buf.getvalue()


def _strip_markdown(text: str) -> str:
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}(.*?)_{1,2}', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'`{1,3}.*?`{1,3}', '', text, flags=re.DOTALL)
    return text.strip()


# Common acronyms that should stay uppercase so Smallest.ai pronounces them letter-by-letter.
_ACRONYM_ALLOWLIST: frozenset[str] = frozenset({
    "RGV", "AI", "AGI", "USA", "UK", "US", "UAE", "UN", "EU",
    "TV", "DVD", "CD", "OK", "FBI", "CIA", "NASA", "BMW", "BBC",
    "IPL", "BCCI", "BJP", "INC", "RSS", "PM", "CM", "MLA", "MP",
    "GDP", "CEO", "CTO", "CFO", "HR", "IT", "PR", "FAQ", "AKA",
    "FYI", "DIY", "ETA", "KPI", "ROI", "NGO", "VFX", "SFX",
})
_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _normalize_for_tts(text: str) -> str:
    """Lowercase all-caps non-acronym words so Smallest.ai reads them as words, not letters."""
    def repl(m: re.Match) -> str:
        w = m.group(0)
        if w.isupper() and w not in _ACRONYM_ALLOWLIST:
            return w.capitalize()
        return w
    return _WORD_RE.sub(repl, text)


def _prepend_silence(wav_bytes: bytes, ms: int = 80, sample_rate: int = 24000) -> bytes:
    """Prepend ms milliseconds of silence to a WAV, patching RIFF/data size headers."""
    if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF":
        return wav_bytes
    pad = b"\x00" * (int(sample_rate * ms / 1000) * 2)  # mono 16-bit
    header = bytearray(wav_bytes[:44])
    body = wav_bytes[44:]
    new_riff_size = (36 + len(body) + len(pad)).to_bytes(4, "little")
    new_data_size = (len(body) + len(pad)).to_bytes(4, "little")
    header[4:8] = new_riff_size
    header[40:44] = new_data_size
    return bytes(header) + pad + body


def _resolve_voice(language: str) -> str:
    _settings_map = {"en": settings.voice_id_en, "te": settings.voice_id_te, "hi": settings.voice_id_hi}
    from_settings = _settings_map.get(language, "")
    if from_settings:
        return from_settings
    return _VOICE_MAP.get(language, _VOICE_MAP["en"])


async def _synthesise_chunk(text: str, voice_id: str) -> bytes:
    async with _SEMAPHORE:
        response = await _http_client.post(
            SMALLEST_URL,
            headers={
                "Authorization": f"Bearer {settings.smallest_ai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "voice_id": voice_id,
                "sample_rate": 24000,
                "speed": settings.tts_speed,
                "output_format": "wav",
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"Smallest.ai error {response.status_code}: {response.text[:200]}")
        return response.content


# ── Public synthesis API ──────────────────────────────────────────────────────


async def synthesise_sentence(text: str, language: str, prepend_silence: bool = False) -> bytes:
    """Synthesize a single pre-split sentence for streaming TTS."""
    text = _normalize_for_tts(_strip_markdown(text))
    if not text:
        return b""
    voice_id = _resolve_voice(language)
    wav = await _synthesise_chunk(text, voice_id)
    if prepend_silence:
        wav = _prepend_silence(wav)
    return wav


async def synthesise_speech(text: str, language: str) -> bytes:
    """Split text into ≤250-char chunks, call Smallest.ai in parallel, return merged WAV."""
    voice_id = _resolve_voice(language)
    chunks = _split_chunks(_normalize_for_tts(_strip_markdown(text)))
    wav_chunks = await asyncio.gather(
        *[_synthesise_chunk(chunk, voice_id) for chunk in chunks]
    )
    if len(wav_chunks) == 1:
        return wav_chunks[0]
    return _concat_wavs(list(wav_chunks))
