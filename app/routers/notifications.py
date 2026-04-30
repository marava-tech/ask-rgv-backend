from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.auth import require_user
from db import queries

router = APIRouter(prefix="/notifications", tags=["notifications"])


class FcmTokenRequest(BaseModel):
    token: str


@router.post("/fcm-token", status_code=204)
async def register_fcm_token(
    body: FcmTokenRequest,
    user: dict = Depends(require_user),
):
    """Called by the Flutter app whenever Firebase issues a new FCM token."""
    await queries.update_user_fcm_token(user["sub"], body.token)
