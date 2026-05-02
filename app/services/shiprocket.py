import logging
import httpx
from core.config import settings

_log = logging.getLogger(__name__)
_SHIPROCKET_BASE = "https://apiv2.shiprocket.in/v1/external"


async def _request(method: str, path: str, **kwargs) -> dict:
    headers = {
        "Authorization": f"Bearer {settings.shiprocket_token}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, f"{_SHIPROCKET_BASE}{path}", headers=headers, **kwargs)
        if resp.status_code == 401:
            _log.error("[shiprocket] 401 Unauthorized — rotate SHIPROCKET_TOKEN in .env")
            raise RuntimeError("Shiprocket token invalid or expired — rotate SHIPROCKET_TOKEN in .env")
        _log.info("[shiprocket] %s %s → %d: %s", method, path, resp.status_code, resp.text[:500])
        resp.raise_for_status()
        return resp.json()


async def create_shipment(order: dict, product: dict) -> dict:
    address = order.get("shipping_address", {})
    variant_sku = ""
    for v in (product.get("variants") or []):
        if v.get("id") == order["variant_id"]:
            variant_sku = v.get("sku", "")
            break

    payload = {
        "order_id": str(order["id"]),
        "order_date": order["created_at"].isoformat() if hasattr(order.get("created_at"), "isoformat") else str(order.get("created_at", "")),
        "pickup_location": "Primary",
        "billing_customer_name": address.get("name", ""),
        "billing_address": address.get("address_line1", ""),
        "billing_city": address.get("city", ""),
        "billing_pincode": address.get("pincode", ""),
        "billing_state": address.get("state", ""),
        "billing_country": address.get("country", "India"),
        "billing_email": "",
        "billing_phone": address.get("phone", ""),
        "shipping_is_billing": True,
        "order_items": [
            {
                "name": product.get("name", ""),
                "sku": variant_sku,
                "units": 1,
                "selling_price": str(order["amount_inr"] / 100),
            }
        ],
        "payment_method": "Prepaid",
        "sub_total": str(order["amount_inr"] / 100),
        "weight": 0.5,
    }
    return await _request("POST", "/orders/create/adhoc", json=payload)


async def track_shipment(awb: str) -> dict:
    return await _request("GET", f"/courier/track/awb/{awb}")
