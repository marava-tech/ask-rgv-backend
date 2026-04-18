from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import Response
from pydantic import BaseModel

from core.auth import get_current_user
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


@router.post("/stt")
async def speech_to_text(
    audio: UploadFile = File(...),
    user: dict | None = Depends(get_current_user),
):
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

    if not result["transcript"]:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="No speech detected in audio")

    return result


@router.post("/tts")
async def text_to_speech(
    body: TTSRequest,
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

    text = body.text
    language = body.language if body.language in ("en", "te", "hi") else "en"

    audio_bytes = await synthesise_speech(text, language)
    return Response(content=audio_bytes, media_type="audio/mpeg")
