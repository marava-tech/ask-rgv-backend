import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from core.auth import require_user
from core.config import settings
from db import queries
from models.schemas import (
    ConfirmOrderRequest,
    InitiateOrderRequest,
    InitiateOrderResponse,
)

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/merch", tags=["merch"])


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

    variant_ids = [v["id"] for v in product.get("variants", [])]
    if body.variant_id not in variant_ids:
        raise HTTPException(status_code=400, detail="Invalid variant")

    price = product["price_inr"]
    discount = 0
    promo_code = None

    if body.promo_code:
        waitlist_row = await queries.get_waitlist_by_promo_code(body.promo_code)
        if not waitlist_row:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_promo_code", "message": "Invalid or already used promo code"},
            )
        discount = int(price * 0.20)
        promo_code = body.promo_code

    final_amount = price - discount

    import httpx
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
        original_amount_inr=price,
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

    return {"order_id": body.order_id, "status": "paid"}
