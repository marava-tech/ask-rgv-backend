import json
import logging

from core.config import settings

logger = logging.getLogger(__name__)

_play_service = None


def _get_play_service():
    global _play_service
    if _play_service is not None:
        return _play_service
    if not settings.google_service_account_json:
        return None
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            settings.google_service_account_json,
            scopes=["https://www.googleapis.com/auth/androidpublisher"],
        )
        _play_service = build("androidpublisher", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.error("[iap] Failed to initialize Play service: %s", e)
    return _play_service


async def verify_subscription_purchase(product_id: str, purchase_token: str) -> dict:
    """Verify a subscription purchase token with Google Play Developer API.
    Returns dict with 'valid' bool and 'expiry_time_millis' if valid."""
    service = _get_play_service()
    if service is None:
        logger.warning("[iap] Play service not configured — skipping verification (dev mode)")
        return {
            "valid": True, 
            "expiry_time_millis": None,
            "state": "SUBSCRIPTION_STATE_ACTIVE",
            "base_plan_id": None
        }

    try:
        result = (
            service.purchases()
            .subscriptionsv2()
            .get(
                packageName=settings.google_play_package_name,
                token=purchase_token,
            )
            .execute()
        )
        # subscriptionState: ACTIVE, CANCELED, IN_GRACE_PERIOD, ON_HOLD, PAUSED, EXPIRED
        state = result.get("subscriptionState", "")
        valid = state in ("SUBSCRIPTION_STATE_ACTIVE", "SUBSCRIPTION_STATE_IN_GRACE_PERIOD")
        expiry_millis = None
        base_plan_id = None
        lines = result.get("lineItems", [])
        if lines:
            expiry_millis = lines[0].get("expiryTime")
            base_plan_id = lines[0].get("productId")
        return {
            "valid": valid, 
            "expiry_time_millis": expiry_millis, 
            "state": state,
            "base_plan_id": base_plan_id
        }
    except Exception as e:
        logger.error("[iap] verify_subscription_purchase error: %s", e)
        return {"valid": False}


async def verify_one_time_purchase(product_id: str, purchase_token: str) -> dict:
    """Verify a one-time (consumable) product purchase token."""
    service = _get_play_service()
    if service is None:
        logger.warning("[iap] Play service not configured — skipping verification (dev mode)")
        return {"valid": True}

    try:
        result = (
            service.purchases()
            .products()
            .get(
                packageName=settings.google_play_package_name,
                productId=product_id,
                token=purchase_token,
            )
            .execute()
        )
        # purchaseState: 0=PURCHASED, 1=CANCELED, 2=PENDING
        purchase_state = result.get("purchaseState", 0)
        return {"valid": purchase_state == 0, "order_id": result.get("orderId")}
    except Exception as e:
        logger.error("[iap] verify_one_time_purchase error: %s", e)
        return {"valid": False}
