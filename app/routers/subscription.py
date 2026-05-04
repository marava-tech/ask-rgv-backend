import json
import base64
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.auth import get_current_user, require_user
from db import queries
from models.schemas import (
    IAPVerifyRequest,
    IAPVerifyResponse,
    PackVerifyRequest,
    PackVerifyResponse,
)
from services.quota import (
    get_quota_remaining,
    get_tier_limit_seconds,
    limit_to_credits,
    next_credit_refresh_utc,
    seconds_to_credits,
)
from services.iap import verify_subscription_purchase, verify_one_time_purchase

router = APIRouter(prefix="/subscription", tags=["subscription"])

# ── Product ID → tier mapping ─────────────────────────────────────────────────

_SUBSCRIPTION_PRODUCT_TIERS = {
    "ask_rgv_fan": "fan",
    "ask_rgv_super": "super_fan",
    "ask_rgv_fan_monthly": "fan",
    "ask_rgv_fan_annual": "fan",
    "ask_rgv_super_monthly": "super_fan",
    "ask_rgv_super_annual": "super_fan",
    "ask-rgv-fan-monthly": "fan",
    "ask-rgv-fan-annual": "fan",
    "ask-rgv-super-monthly": "super_fan",
    "ask-rgv-super-annual": "super_fan",
}

_PACK_TURNS = {
    "ask_rgv_pack_5": 5,
    "ask_rgv_pack_12": 12,
}

_ANNUAL_PRODUCTS = {
    "ask_rgv_fan_annual", 
    "ask_rgv_super_annual",
    "ask-rgv-fan-annual",
    "ask-rgv-super-annual",
}


@router.get("/status")
async def subscription_status(
    device_id: str | None = None,
    user: dict | None = Depends(get_current_user),
):
    if user is None:
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id required for anonymous users")
        tier = "anonymous"
        quota_remaining = await get_quota_remaining(None, device_id, tier)
        limit = await get_tier_limit_seconds(tier)
        used = max(0, limit - quota_remaining) if limit >= 0 else 0
        return {
            "tier": tier,
            "remaining_seconds": quota_remaining,
            "used_seconds": used,
            "total_seconds": limit,
            "limit_seconds": limit,
            "remaining_credits": seconds_to_credits(quota_remaining),
            "limit_credits": limit_to_credits(limit),
            "exhausted": quota_remaining == 0,
            "current_period_end": None,
            "next_credit_refresh": next_credit_refresh_utc().isoformat(),
        }
    user_data = await queries.get_user_by_id(user["sub"])
    raw_tier = user_data["tier"] if user_data else "free"
    tier = "seeker" if raw_tier == "free" else raw_tier
    quota_remaining = await get_quota_remaining(user["sub"], None, tier)
    sub = await queries.get_active_subscription(user["sub"])
    limit = await get_tier_limit_seconds(tier)
    limit_seconds = limit if limit != -1 else -1
    used = max(0, limit_seconds - quota_remaining) if limit_seconds >= 0 else 0
    return {
        "tier": tier,
        "remaining_seconds": quota_remaining,
        "used_seconds": used,
        "total_seconds": limit_seconds,
        "limit_seconds": limit_seconds if limit_seconds >= 0 else 999999,
        "remaining_credits": seconds_to_credits(quota_remaining) if quota_remaining >= 0 else -1,
        "limit_credits": limit_to_credits(limit),
        "exhausted": quota_remaining == 0,
        "current_period_end": sub["current_period_end"].isoformat() if sub else None,
        "next_credit_refresh": next_credit_refresh_utc().isoformat(),
    }


@router.post("/verify", response_model=IAPVerifyResponse)
async def verify_subscription(body: IAPVerifyRequest, user: dict = Depends(require_user)):
    """Receive a Google Play purchase token, verify with Play Developer API, activate subscription."""
    # Check if the product_id is at least known as a parent subscription or a specific base plan
    if body.product_id not in _SUBSCRIPTION_PRODUCT_TIERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown product: {body.product_id}",
        )

    purchase = await verify_subscription_purchase(body.product_id, body.purchase_token)
    if not purchase.get("valid"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Purchase token could not be verified with Google Play",
        )

    # Use the specific base plan ID from Google Play if available to determine tier and period accurately
    effective_product_id = purchase.get("base_plan_id") or body.product_id
    tier = _SUBSCRIPTION_PRODUCT_TIERS.get(effective_product_id) or _SUBSCRIPTION_PRODUCT_TIERS.get(body.product_id)
    
    if not tier:
        # Should not happen given the initial check, but for safety:
        tier = "fan" if "fan" in effective_product_id else "super_fan"

    period = "annual" if (effective_product_id in _ANNUAL_PRODUCTS or "annual" in str(effective_product_id)) else "monthly"
    
    await queries.activate_iap_subscription(
        user_id=user["sub"],
        tier=tier,
        purchase_token=body.purchase_token,
        product_id=effective_product_id,
        subscription_period=period,
        expiry_time_millis=purchase.get("expiry_time_millis"),
    )
    return IAPVerifyResponse(tier=tier, success=True)


@router.post("/webhook", status_code=200)
async def rtdn_webhook(request: Request):
    """Google Play Real-Time Developer Notifications via Pub/Sub push."""
    body = await request.json()
    # Pub/Sub push delivers base64-encoded message data
    message = body.get("message", {})
    data_b64 = message.get("data", "")
    if not data_b64:
        return {"status": "ok"}

    try:
        notification = json.loads(base64.b64decode(data_b64).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Pub/Sub message")

    sub_notif = notification.get("subscriptionNotification", {})
    notification_type = sub_notif.get("notificationType")
    purchase_token = sub_notif.get("purchaseToken")
    product_id = sub_notif.get("subscriptionId")

    if not purchase_token:
        return {"status": "ok"}

    # notificationType constants: 1=RECOVERED, 2=RENEWED, 3=CANCELED, 4=PURCHASED,
    # 12=EXPIRED, 13=PAUSED (etc.) — handle renewal, cancel, expire
    if notification_type in (2, 4):  # RENEWED or PURCHASED
        purchase = await verify_subscription_purchase(product_id or "", purchase_token)
        if purchase.get("valid"):
            effective_product_id = purchase.get("base_plan_id") or product_id or ""
            tier = _SUBSCRIPTION_PRODUCT_TIERS.get(effective_product_id) or _SUBSCRIPTION_PRODUCT_TIERS.get(product_id or "")
            if tier:
                period = "annual" if (effective_product_id in _ANNUAL_PRODUCTS or "annual" in str(effective_product_id)) else "monthly"
                await queries.activate_iap_subscription_by_token(
                    purchase_token=purchase_token,
                    tier=tier,
                    product_id=effective_product_id,
                    subscription_period=period,
                    expiry_time_millis=purchase.get("expiry_time_millis"),
                )
    elif notification_type in (3, 12):  # CANCELED or EXPIRED
        await queries.expire_subscription_by_token(purchase_token)

    return {"status": "ok"}


@router.post("/pack/verify", response_model=PackVerifyResponse)
async def verify_pack(body: PackVerifyRequest, user: dict = Depends(require_user)):
    """Verify a one-time conversation pack purchase and credit turns."""
    turns = _PACK_TURNS.get(body.product_id)
    if not turns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown pack product: {body.product_id}",
        )

    purchase = await verify_one_time_purchase(body.product_id, body.purchase_token)
    if not purchase.get("valid"):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Pack purchase could not be verified with Google Play",
        )

    already_used = await queries.is_pack_token_used(body.purchase_token)
    if already_used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This purchase token has already been redeemed",
        )

    await queries.credit_quota_pack(
        user_id=user["sub"],
        product_id=body.product_id,
        purchase_token=body.purchase_token,
        turns=turns,
    )
    return PackVerifyResponse(turns_credited=turns, success=True)


@router.post("/continue-tomorrow", status_code=202)
async def continue_tomorrow(user: dict = Depends(require_user)):
    """Schedule an FCM return notification at next IST midnight for this user."""
    from datetime import timedelta

    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    tomorrow_ist = (now + timedelta(days=1)).replace(
        hour=0, minute=1, second=0, microsecond=0
    )

    user_data = await queries.get_user_by_id(user["sub"])
    fcm_token = user_data.get("fcm_token") if user_data else None
    if not fcm_token:
        return {"scheduled": False, "reason": "no_fcm_token"}

    # Schedule via APScheduler (reuse existing scheduler in app)
    try:
        from main import scheduler
        job_id = f"continue_tomorrow:{user['sub']}"
        scheduler.add_job(
            _send_continue_tomorrow_push,
            "date",
            run_date=tomorrow_ist,
            args=[fcm_token],
            id=job_id,
            replace_existing=True,
        )
        logger.info("[paywall] continue-tomorrow scheduled for %s at %s IST", user["sub"], tomorrow_ist)
    except Exception as e:
        logger.warning("[paywall] could not schedule continue-tomorrow: %s", e)
        return {"scheduled": False, "reason": str(e)}

    return {"scheduled": True, "at": tomorrow_ist.isoformat()}


async def _send_continue_tomorrow_push(fcm_token: str) -> None:
    """Send FCM push notification to bring user back."""
    import httpx
    from core.config import settings
    import google.auth.transport.requests as google_requests
    from google.oauth2 import service_account

    try:
        creds = service_account.Credentials.from_service_account_file(
            settings.firebase_service_account_path,
            scopes=["https://www.googleapis.com/auth/firebase.messaging"],
        )
        req = google_requests.Request()
        creds.refresh(req)

        async with httpx.AsyncClient() as client:
            await client.post(
                "https://fcm.googleapis.com/v1/projects/askrgv/messages:send",
                headers={"Authorization": f"Bearer {creds.token}"},
                json={
                    "message": {
                        "token": fcm_token,
                        "notification": {
                            "title": "RGV is waiting.",
                            "body": "Your 3 daily turns just reset. Pick up where you left off.",
                        },
                        "android": {"priority": "high"},
                    }
                },
            )
    except Exception as e:
        logger.error("[paywall] FCM push failed: %s", e)
