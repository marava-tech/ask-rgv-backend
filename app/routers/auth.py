from datetime import timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import (
    create_access_token,
    create_admin_token,
    generate_refresh_token,
    hash_token,
    require_user,
    verify_google_token,
)
from core.config import settings
from db import queries
from models.schemas import AdminLoginRequest, GoogleAuthRequest, RefreshRequest, TokenResponse, UserInfo
from services.quota import blacklist_jwt, is_jwt_blacklisted

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=TokenResponse)
async def google_auth(body: GoogleAuthRequest):
    try:
        info = verify_google_token(body.id_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")

    user = await queries.upsert_user(
        google_id=info["sub"],
        email=info["email"],
        display_name=info.get("name", ""),
        avatar_url=info.get("picture", ""),
    )
    user_id = str(user["id"])
    tier = user["tier"]

    access_token = create_access_token(user_id, tier)
    raw_refresh, hashed_refresh, expires = generate_refresh_token()
    await queries.store_refresh_token(user_id, hashed_refresh, expires)

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=UserInfo(
            id=user_id,
            email=info.get("email"),
            name=info.get("name"),
            avatar_url=info.get("picture"),
            tier=tier,
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    hashed = hash_token(body.refresh_token)
    row = await queries.consume_refresh_token(hashed)
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    user_id = str(row["user_id"])
    user = await queries.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token = create_access_token(user_id, user["tier"])
    raw_refresh, hashed_refresh, expires = generate_refresh_token()
    await queries.store_refresh_token(user_id, hashed_refresh, expires)

    return TokenResponse(access_token=access_token, refresh_token=raw_refresh)


@router.post("/logout", status_code=204)
async def logout(user: dict = Depends(require_user)):
    jti = user.get("jti")
    exp = user.get("exp")
    if jti and exp:
        from datetime import datetime
        ttl = max(0, exp - int(datetime.now(timezone.utc).timestamp()))
        await blacklist_jwt(jti, ttl)
    await queries.delete_user_refresh_tokens(user["sub"])
