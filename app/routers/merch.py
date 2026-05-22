import asyncio
import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import require_user
from core.config import settings
from db import queries
from models.schemas import (
    ConfirmOrderRequest,
    InitiateOrderRequest,
    InitiateOrderResponse,
    PromoValidateResponse,
)
from services import shiprocket as shiprocket_service

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/merch", tags=["merch"])


@router.get("/promo/validate", response_model=PromoValidateResponse)
async def validate_promo_code(
    code: str,
    product_id: str | None = None,
    user: dict = Depends(require_user),
):
    waitlist_row = await queries.get_waitlist_by_merch_code_with_email(code)
    if not waitlist_row:
        return PromoValidateResponse(valid=False, error_type="invalid_code")

    if waitlist_row["merch_code_redeemed_at"] is not None:
        return PromoValidateResponse(valid=False, error_type="already_used")

    user_record = await queries.get_user_by_id(user["sub"])
    user_email = user_record["email"] if user_record else None
    if user_email != waitlist_row["email"]:
        return PromoValidateResponse(valid=False, error_type="not_yours")

    discount_amount_inr = None
    if product_id:
        product = await queries.get_merch_product(product_id)
        if product:
            discount_amount_inr = int(product["price_inr"] * 0.20)

    return PromoValidateResponse(
        valid=True,
        discount_percent=20,
        discount_amount_inr=discount_amount_inr,
        error_type=None,
    )


@router.get("/products")
async def list_products():
    products = await queries.list_merch_products(enabled_only=True)
    return {"products": products}


@router.post("/orders/initiate", response_model=InitiateOrderResponse)
async def initiate_order(body: InitiateOrderRequest, user: dict = Depends(require_user)):
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    product = await queries.get_merch_product(body.product_id)
    if not product or not product["enabled"]:
        raise HTTPException(status_code=404, detail="Product not found")

    if product.get("is_out_of_stock"):
        raise HTTPException(status_code=409, detail="product_out_of_stock")

    variant_ids = [v["id"] for v in product.get("variants", [])]
    if body.variant_id not in variant_ids:
        raise HTTPException(status_code=400, detail="Invalid variant")

    # Server-authoritative pricing: use effective_price_inr (presale price or full price)
    # Promo codes and presale discounts are separate systems (non-stackable per spec)
    original_price = product["price_inr"]
    price = product["effective_price_inr"]
    discount = 0
    promo_code = None

    if body.promo_code:
        waitlist_row = await queries.get_waitlist_by_merch_code_with_email(body.promo_code)
        if not waitlist_row or waitlist_row["merch_code_redeemed_at"] is not None:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_promo_code", "message": "Invalid or already used promo code"},
            )
        user_record = await queries.get_user_by_id(user["sub"])
        user_email = user_record["email"] if user_record else None
        if user_email != waitlist_row["email"]:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_promo_code", "message": "Invalid or already used promo code"},
            )
        discount = int(price * 0.20)
        promo_code = body.promo_code

    final_amount = price - discount

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.razorpay.com/v1/orders",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={"amount": final_amount, "currency": "INR"},
        )
        if resp.status_code != 200:
            _log.error("[merch] Razorpay order creation failed: %s", resp.text)
            raise HTTPException(status_code=502, detail="Payment gateway error")
        rzp_order = resp.json()

    order = await queries.create_merch_order(
        user_id=user["sub"],
        product_id=body.product_id,
        variant_id=body.variant_id,
        amount_inr=final_amount,
        original_amount_inr=original_price,
        promo_code=promo_code,
        razorpay_order_id=rzp_order["id"],
    )

    return InitiateOrderResponse(
        order_id=order["id"],
        razorpay_order_id=rzp_order["id"],
        amount=final_amount,
        key_id=settings.razorpay_key_id,
    )


@router.post("/orders/confirm")
async def confirm_order(body: ConfirmOrderRequest, user: dict = Depends(require_user)):
    order = await queries.get_merch_order(body.order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if str(order["user_id"]) != user["sub"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    if order["status"] != "pending":
        raise HTTPException(status_code=400, detail="Order already processed")

    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{body.razorpay_order_id}|{body.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(
            status_code=400,
            detail={"code": "payment_verification_failed", "message": "Payment verification failed"},
        )

    await queries.confirm_merch_order(
        order_id=body.order_id,
        razorpay_payment_id=body.razorpay_payment_id,
        shipping_address=body.shipping_address.model_dump(),
    )

    if order.get("promo_code"):
        await queries.redeem_merch_promo_code(order["promo_code"])

    asyncio.create_task(_auto_fulfill(body.order_id))

    return {"order_id": body.order_id, "status": "paid"}


async def _auto_fulfill(order_id: str) -> None:
    try:
        order = await queries.get_merch_order(order_id)
        if not order:
            _log.error("[merch] auto-fulfill: order %s not found", order_id)
            return
        product = await queries.get_merch_product(order["product_id"])
        if not product:
            _log.error("[merch] auto-fulfill: product not found for order %s", order_id)
            return
        result = await shiprocket_service.create_shipment(order, product)
        shiprocket_order_id = str(result.get("order_id", ""))
        shiprocket_awb = str(result.get("awb_code", ""))
        await queries.auto_confirm_merch_order(
            order_id=order_id,
            shiprocket_order_id=shiprocket_order_id,
            shiprocket_awb=shiprocket_awb,
        )
    except Exception as e:
        _log.error("[merch] auto-fulfill failed for order %s: %s", order_id, e)


@router.get("/orders")
async def list_my_orders(user: dict = Depends(require_user)):
    orders = await queries.get_user_orders(user["sub"])
    return orders


@router.get("/orders/{order_id}/tracking")
async def get_order_tracking(order_id: str, user: dict = Depends(require_user)):
    order = await queries.get_merch_order(order_id)
    if not order or str(order["user_id"]) != user["sub"]:
        raise HTTPException(status_code=404, detail="Order not found")

    awb = order.get("shiprocket_awb")
    if not awb:
        return {"status": "pending_fulfillment", "tracking_events": []}

    try:
        raw = await shiprocket_service.track_shipment(awb)
        tracking_data = raw.get("tracking_data", {})
        shipment_track = tracking_data.get("shipment_track", [{}])
        track = shipment_track[0] if shipment_track else {}
        activities = tracking_data.get("shipment_track_activities", [])
        return {
            "awb": awb,
            "courier_name": track.get("courier_name", ""),
            "current_status": track.get("current_status", ""),
            "tracking_events": [
                {
                    "date": a.get("date", ""),
                    "activity": a.get("activity", ""),
                    "location": a.get("location", ""),
                }
                for a in activities
            ],
        }
    except Exception as e:
        _log.error("[merch] tracking failed for AWB %s: %s", awb, e)
        raise HTTPException(status_code=502, detail="tracking_unavailable")
