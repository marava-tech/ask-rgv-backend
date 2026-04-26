import json
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.auth import get_current_user, require_user
from core.config import settings
from db import queries
from models.schemas import CreateOrderRequest, CreateOrderResponse, UpgradePriceResponse
from services.quota import (
    get_quota_remaining,
    get_tier_limit_seconds,
    limit_to_credits,
    next_credit_refresh_utc,
    seconds_to_credits,
)
from services.razorpay import (
    TIER_AMOUNTS,
    create_order,
    create_upgrade_order,
    verify_webhook_signature,
)

router = APIRouter(prefix="/subscription", tags=["subscription"])

_FAN_PRICE_PAISE = TIER_AMOUNTS["fan"]          # 9900
_SUPER_FAN_PRICE_PAISE = TIER_AMOUNTS["super_fan"]  # 29900
_PLAN_DAYS = 30


def _calc_prorated_upgrade(period_end: datetime) -> dict:
    """Return prorated Super Fan upgrade price for a Fan subscriber."""
    now = datetime.now(timezone.utc)
    remaining_seconds = max(0, (period_end - now).total_seconds())
    remaining_days = remaining_seconds / 86400          # fractional days
    remaining_days_int = math.ceil(remaining_days)      # shown to user
    fan_credit_paise = (remaining_days / _PLAN_DAYS) * _FAN_PRICE_PAISE
    raw_upgrade_paise = _SUPER_FAN_PRICE_PAISE - fan_credit_paise
    # Round to nearest ₹1 (100 paise), never below ₹1
    upgrade_paise = max(100, round(raw_upgrade_paise / 100) * 100)
    discount_rupees = round(fan_credit_paise / 100)
    return {
        "prorated_amount_paise": upgrade_paise,
        "prorated_amount_rupees": upgrade_paise // 100,
        "remaining_days": remaining_days_int,
        "discount_rupees": discount_rupees,
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
        return {
            "tier": tier,
            "remaining_seconds": quota_remaining,
            "limit_seconds": limit,
            "remaining_credits": seconds_to_credits(quota_remaining),
            "limit_credits": limit_to_credits(limit),
            "exhausted": quota_remaining == 0,
            "current_period_end": None,
            "next_credit_refresh": next_credit_refresh_utc().isoformat(),
        }
    user_data = await queries.get_user_by_id(user["sub"])
    tier = user_data["tier"] if user_data else "free"
    quota_remaining = await get_quota_remaining(user["sub"], None, tier)
    sub = await queries.get_active_subscription(user["sub"])
    limit = await get_tier_limit_seconds(tier)
    limit_seconds = limit if limit != -1 else 999999
    return {
        "tier": tier,
        "remaining_seconds": quota_remaining,
        "limit_seconds": limit_seconds,
        "remaining_credits": seconds_to_credits(quota_remaining) if quota_remaining >= 0 else -1,
        "limit_credits": limit_to_credits(limit),
        "exhausted": quota_remaining == 0,
        "current_period_end": sub["current_period_end"].isoformat() if sub else None,
        "next_credit_refresh": next_credit_refresh_utc().isoformat(),
    }


@router.get("/upgrade-price", response_model=UpgradePriceResponse)
async def get_upgrade_price(user: dict = Depends(require_user)):
    """Returns the prorated Fan → Super Fan upgrade price for the current user."""
    user_data = await queries.get_user_by_id(user["sub"])
    if not user_data or user_data["tier"] != "fan":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upgrade price only available for Fan subscribers.",
        )
    sub = await queries.get_active_subscription(user["sub"])
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active Fan subscription found.",
        )
    period_end: datetime = sub["current_period_end"]
    if period_end.tzinfo is None:
        period_end = period_end.replace(tzinfo=timezone.utc)
    calc = _calc_prorated_upgrade(period_end)
    return UpgradePriceResponse(
        **calc,
        period_end=period_end.isoformat(),
    )


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_subscription_order(body: CreateOrderRequest, user: dict = Depends(require_user)):
    # Block re-purchasing the same tier
    user_data = await queries.get_user_by_id(user["sub"])
    current_tier = user_data["tier"] if user_data else "free"
    if body.tier == current_tier and current_tier in ("fan", "super_fan"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Already subscribed to {body.tier}.",
        )

    if body.is_upgrade and body.tier == "super_fan" and current_tier == "fan":
        # Prorated upgrade: calculate discount from remaining Fan days
        sub = await queries.get_active_subscription(user["sub"])
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active Fan subscription found for upgrade.",
            )
        period_end: datetime = sub["current_period_end"]
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)
        calc = _calc_prorated_upgrade(period_end)
        order = await create_upgrade_order(calc["prorated_amount_paise"])
        expected_paise = calc["prorated_amount_paise"]
    else:
        order = await create_order(body.tier)
        expected_paise = TIER_AMOUNTS.get(body.tier)

    await queries.create_subscription_order(user["sub"], body.tier, order["id"], expected_paise)
    return CreateOrderResponse(order_id=order["id"], amount=order["amount"], key_id=settings.razorpay_key_id)


@router.post("/webhook", status_code=200)
async def razorpay_webhook(request: Request):
    payload_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not verify_webhook_signature(payload_bytes, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    data = json.loads(payload_bytes)
    event = data.get("event")

    if event == "payment.captured":
        payment = data["payload"]["payment"]["entity"]
        order_id = payment.get("order_id")

        sub_row = await queries.get_subscription_by_order_id(order_id)
        if not sub_row:
            return {"status": "ok"}

        # Verify exact captured amount against what was stored at order creation.
        expected_paise = sub_row.get("expected_amount_paise")
        captured_amount = payment.get("amount")
        if expected_paise and captured_amount != expected_paise:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Amount mismatch: expected {expected_paise}, got {captured_amount}",
            )

        # Atomic: activates subscription AND updates user.tier in one transaction.
        await queries.activate_subscription_and_update_tier(
            payment_id=payment["id"],
            order_id=order_id,
        )

    return {"status": "ok"}
