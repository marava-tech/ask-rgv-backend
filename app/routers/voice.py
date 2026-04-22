from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import Response
from pydantic import BaseModel

from core.auth import get_current_user
from db import queries
from services.prompt import estimate_turn_duration
from services.quota import add_quota_usage, get_quota_remaining
from services.stt import transcribe_audio
from services.tts import synthesise_speech

router = APIRouter(prefix="/voice", tags=["voice"])

ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg", "audio/ogg",
    "audio/wav", "audio/x-wav", "audio/flac", "audio/aac",
}
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB


class TTSRequest(BaseModel):
    text: str
    language: str = "en"


async def _get_tier(user: dict | None, device_id: str | None) -> tuple[str, str | None, str | None]:
    """Returns (tier, user_id, device_id) for quota operations."""
    if user:
        user_data = await queries.get_user_by_id(user["sub"])
        tier = user_data["tier"] if user_data else "free"
        return tier, user["sub"], None
    return "anonymous", None, device_id


@router.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    device_id: str | None = None,
    user: dict | None = Depends(get_current_user),
):
    # Bug #36: voice endpoints had no quota check — users could burn Deepgram spend freely
    tier, user_id, dev_id = await _get_tier(user, device_id)
    quota_remaining = await get_quota_remaining(user_id, dev_id, tier)
    if quota_remaining == 0:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="quota_exhausted")

    if audio.content_type and audio.content_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail=f"Unsupported audio type: {audio.content_type}")

    audio_bytes = await audio.read()
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="Audio file too large (max 10 MB)")
    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty audio file")

    content_type = audio.content_type or "audio/webm"
    result = await transcribe_audio(audio_bytes, content_type)

    return result


@router.post("/tts")
async def text_to_speech(
    body: TTSRequest,
    device_id: str | None = None,
    user: dict | None = Depends(get_current_user),
):
    if not body.text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty text")

    # Bug #42: text was silently truncated to 2000 chars with no feedback to caller
    if len(body.text) > 2000:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Text too long — maximum 2000 characters",
        )

    # Bug #36: no quota check or deduction on TTS — estimate duration from text length
    tier, user_id, dev_id = await _get_tier(user, device_id)
    quota_remaining = await get_quota_remaining(user_id, dev_id, tier)
    if quota_remaining == 0:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="quota_exhausted")

    text = body.text
    language = body.language if body.language in ("en", "te", "hi") else "en"

    try:
        audio_bytes = await synthesise_speech(text, language)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    tts_seconds = estimate_turn_duration(text)
    await add_quota_usage(user_id, dev_id, tts_seconds)

    return Response(content=audio_bytes, media_type="audio/wav")
