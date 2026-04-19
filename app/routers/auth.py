from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import (
    create_access_token,
    generate_refresh_token,
    hash_token,
    require_user,
    verify_google_token,
)
from core.config import settings
from db import queries
from models.schemas import GoogleAuthRequest, LogoutRequest, RefreshRequest, TokenResponse, UserInfo
from services.quota import blacklist_jwt, is_jwt_blacklisted

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=TokenResponse)
async def google_auth(body: GoogleAuthRequest):
    try:
        # Bug #19: verify_google_token is now async (wraps blocking JWKS call in thread)
        info = await verify_google_token(body.id_token)
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

    # Bug #17: blacklist the old access token if client supplies it, so it can't be reused
    if body.access_token:
        try:
            from core.auth import decode_access_token
            old_payload = decode_access_token(body.access_token)
            old_jti = old_payload.get("jti")
            old_exp = old_payload.get("exp")
            if old_jti and old_exp:
                ttl = max(0, old_exp - int(datetime.now(timezone.utc).timestamp()))
                await blacklist_jwt(old_jti, ttl)
        except Exception:
            pass  # Expired or invalid token — blacklisting not needed

    access_token = create_access_token(user_id, user["tier"])
    raw_refresh, hashed_refresh, expires = generate_refresh_token()
    await queries.store_refresh_token(user_id, hashed_refresh, expires)

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=UserInfo(
            id=user_id,
            email=user.get("email"),
            name=user.get("display_name"),
            avatar_url=user.get("avatar_url"),
            tier=user["tier"],
        ),
    )


@router.post("/logout", status_code=204)
async def logout(body: LogoutRequest, user: dict = Depends(require_user)):
    jti = user.get("jti")
    exp = user.get("exp")
    if jti and exp:
        ttl = max(0, exp - int(datetime.now(timezone.utc).timestamp()))
        await blacklist_jwt(jti, ttl)

    # Bug #22: logout deleted ALL refresh tokens across all devices;
    # if client supplies the specific refresh token, delete only that one
    if body and body.refresh_token:
        hashed = hash_token(body.refresh_token)
        await queries.delete_refresh_token_by_hash(hashed)
    else:
        await queries.delete_user_refresh_tokens(user["sub"])
