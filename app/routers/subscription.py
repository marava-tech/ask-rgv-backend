from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.auth import require_user
from db import queries
from models.schemas import CreateOrderRequest, CreateOrderResponse
from services.quota import TIER_LIMITS, get_quota_remaining
from services.razorpay import (
    create_order,
    subscription_period_end,
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
    return {
        "tier": tier,
        "remaining_seconds": quota_remaining,
        "limit_seconds": limit if limit != -1 else 999999,
        "current_period_end": sub["current_period_end"].isoformat() if sub else None,
    }


@router.post("/create-order", response_model=CreateOrderResponse)
async def create_subscription_order(body: CreateOrderRequest, user: dict = Depends(require_user)):
    order = await create_order(body.tier)
    await queries.create_subscription_order(user["sub"], body.tier, order["id"])
    return CreateOrderResponse(order_id=order["id"], amount=order["amount"])


@router.post("/webhook", status_code=200)
async def razorpay_webhook(request: Request):
    payload_bytes = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    if not verify_webhook_signature(payload_bytes, signature):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

    import json
    data = json.loads(payload_bytes)
    event = data.get("event")

    if event == "payment.captured":
        payment = data["payload"]["payment"]["entity"]
        row = await queries.activate_subscription(
            payment_id=payment["id"],
            order_id=payment["order_id"],
            period_end=subscription_period_end(),
        )
        if row:
            await queries.update_user_tier(str(row["user_id"]), row["tier"])

    return {"status": "ok"}
