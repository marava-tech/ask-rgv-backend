import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import httpx
from core.config import settings
from fastapi import HTTPException

TIER_AMOUNTS = {"fan": 9900, "super_fan": 29900}
RAZORPAY_API = "https://api.razorpay.com/v1"


async def _post_razorpay_order(amount: int) -> dict:
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payment not configured — set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(
                f"{RAZORPAY_API}/orders",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json={"amount": amount, "currency": "INR", "payment_capture": 1},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=502, detail=f"Razorpay error: {e.response.text}")
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="Razorpay unreachable")
    return response.json()


async def create_order(tier: str) -> dict:
    return await _post_razorpay_order(TIER_AMOUNTS[tier])


async def create_upgrade_order(prorated_amount_paise: int) -> dict:
    return await _post_razorpay_order(prorated_amount_paise)


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    msg = f"{order_id}|{payment_id}"
    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        msg.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(payload_bytes: bytes, signature: str) -> bool:
    # Bug #2: empty webhook secret produced a deterministic HMAC that an attacker could forge;
    # fail closed so a misconfigured deploy rejects all webhook events rather than accepting them
    if not settings.razorpay_webhook_secret:
        return False
    expected = hmac.new(
        settings.razorpay_webhook_secret.encode(),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def subscription_period_end() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=30)
