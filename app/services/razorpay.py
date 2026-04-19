import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import httpx
from core.config import settings

TIER_AMOUNTS = {"fan": 9900, "super_fan": 29900}
RAZORPAY_API = "https://api.razorpay.com/v1"


async def create_order(tier: str) -> dict:
    amount = TIER_AMOUNTS[tier]
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            f"{RAZORPAY_API}/orders",
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
            json={"amount": amount, "currency": "INR", "payment_capture": 1},
        )
        response.raise_for_status()
    return response.json()


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
