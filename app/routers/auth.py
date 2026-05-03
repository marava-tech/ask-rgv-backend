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
from db.queries import invalidate_token_family, get_waitlist_by_email
from models.schemas import DeviceTokenRequest, DeviceTokenResponse, GoogleAuthRequest, LogoutRequest, RefreshRequest, TokenResponse, UpdateMeRequest, UserInfo
from services.quota import blacklist_jwt, is_jwt_blacklisted, sign_device_id

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=TokenResponse)
async def google_auth(body: GoogleAuthRequest):
    try:
        # Bug #19: verify_google_token is now async (wraps blocking JWKS call in thread)
        info = await verify_google_token(body.id_token)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")

    if settings.waitlist_gate_enabled:
        waitlist_row = await get_waitlist_by_email(info["email"])
        if not waitlist_row:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "not_on_waitlist", "message": "Early access only"},
            )

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
            preferred_language=user.get("preferred_language"),
            preferred_name=user.get("preferred_name"),
            current_streak=user.get("current_streak") or 0,
            longest_streak=user.get("longest_streak") or 0,
            last_active_date=user.get("last_active_date"),
        ),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    import logging
    _log = logging.getLogger(__name__)

    hashed = hash_token(body.refresh_token)
    row = await queries.consume_refresh_token(hashed)

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "refresh_expired", "message": "Your session has expired — please sign in again"},
        )

    if row.get("replayed"):
        # Token reuse detected: someone is replaying a previously consumed refresh token.
        # Invalidate the entire token family to force re-login for all devices in this chain.
        _log.warning("[auth] refresh token replay detected for family %s", row.get("token_family"))
        await invalidate_token_family(row["token_family"])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "token_reuse_detected", "message": "Security alert: please sign in again"},
        )

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
    # Continue the same token family so a future replay can be traced and invalidated.
    await queries.store_refresh_token(user_id, hashed_refresh, expires, family=row["token_family"])

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        user=UserInfo(
            id=user_id,
            email=user.get("email"),
            name=user.get("display_name"),
            avatar_url=user.get("avatar_url"),
            tier=user["tier"],
            preferred_language=user.get("preferred_language"),
            preferred_name=user.get("preferred_name"),
            current_streak=user.get("current_streak") or 0,
            longest_streak=user.get("longest_streak") or 0,
            last_active_date=user.get("last_active_date"),
            unlocked_themes=user.get("unlocked_themes") or ["default"],
        ),
    )


@router.get("/me", response_model=UserInfo)
async def get_me(user: dict = Depends(require_user)):
    await queries.record_user_activity(user["sub"])
    row = await queries.get_user_by_id(user["sub"])
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return UserInfo(
        id=str(row["id"]),
        email=row.get("email"),
        name=row.get("display_name"),
        avatar_url=row.get("avatar_url"),
        tier=row["tier"],
        preferred_language=row.get("preferred_language"),
        preferred_name=row.get("preferred_name"),
        current_streak=row.get("current_streak") or 0,
        longest_streak=row.get("longest_streak") or 0,
        last_active_date=row.get("last_active_date"),
        unlocked_themes=row.get("unlocked_themes") or ["default"],
    )


@router.patch("/me", response_model=UserInfo)
async def update_me(body: UpdateMeRequest, user: dict = Depends(require_user)):
    await queries.update_user_preferences(user["sub"], body.preferred_language, body.preferred_name)
    row = await queries.get_user_by_id(user["sub"])
    return UserInfo(
        id=str(row["id"]),
        email=row.get("email"),
        name=row.get("display_name"),
        avatar_url=row.get("avatar_url"),
        tier=row["tier"],
        preferred_language=row.get("preferred_language"),
        preferred_name=row.get("preferred_name"),
        current_streak=row.get("current_streak") or 0,
        longest_streak=row.get("longest_streak") or 0,
        last_active_date=row.get("last_active_date"),
        unlocked_themes=row.get("unlocked_themes") or ["default"],
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


@router.post("/device", response_model=DeviceTokenResponse)
async def register_device(body: DeviceTokenRequest):
    """Issue a server-signed device token for anonymous quota tracking.

    The client generates a UUID device_id locally, sends it here once, and
    receives a signed token it must present on all subsequent anonymous requests.
    This prevents forging arbitrary device_ids to claim fresh quota.
    """
    import re
    # Validate the device_id is a well-formed UUID to limit entropy-farming.
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", body.device_id):
        raise HTTPException(status_code=400, detail="device_id must be a lowercase UUID v4")
    device_token = sign_device_id(body.device_id)
    return DeviceTokenResponse(device_id=body.device_id, device_token=device_token)
