import json

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.auth import require_user
from core.config import settings
from db import queries
from models.schemas import CreateOrderRequest, CreateOrderResponse
from services.quota import TIER_LIMITS, get_quota_remaining, seconds_to_credits, limit_to_credits
from services.razorpay import (
    TIER_AMOUNTS,
    create_order,
    verify_webhook_signature,
)

router = APIRouter(prefix="/subscription", tags=["subscription"])


@router.get("/status")
async def subscription_status(user: dict = Depends(require_user)):
    user_data = await queries.get_user_by_id(user["sub"])
    tier = user_data["tier"] if user_data else "free"
    quota_remaining = await get_quota_remaining(user["sub"], None, tier)
    sub = await queries.get_active_subscription(user["sub"])
    limit = TIER_LIMITS.get(tier, 300)
    limit_seconds = limit if limit != -1 else 999999
    return {
        "tier": tier,
        "remaining_seconds": quota_remaining,
        "limit_seconds": limit_seconds,
        "remaining_credits": seconds_to_credits(quota_remaining) if quota_remaining >= 0 else -1,
        "limit_credits": limit_to_credits(limit),
        "current_period_end": sub["current_period_end"].isoformat() if sub else None,
    }


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_subscription_order(body: CreateOrderRequest, user: dict = Depends(require_user)):
    order = await create_order(body.tier)
    await queries.create_subscription_order(user["sub"], body.tier, order["id"])
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

        # Bug #3: never verified captured amount matched the expected tier price —
        # a partial-capture forged event would still upgrade the user
        sub_row = await queries.get_subscription_by_order_id(order_id)
        if not sub_row:
            return {"status": "ok"}

        expected_amount = TIER_AMOUNTS.get(sub_row["tier"])
        if expected_amount and payment.get("amount") != expected_amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Amount mismatch: expected {expected_amount}, got {payment.get('amount')}",
            )

        row = await queries.activate_subscription(
            payment_id=payment["id"],
            order_id=order_id,
        )
        if row:
            await queries.update_user_tier(str(row["user_id"]), row["tier"])

    return {"status": "ok"}
