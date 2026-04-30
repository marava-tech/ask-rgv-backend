import logging
import httpx
from core.config import settings

_log = logging.getLogger(__name__)
_SHIPROCKET_BASE = "https://apiv2.shiprocket.in/v1/external"
_token: str | None = None


async def _authenticate() -> str:
    global _token
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_SHIPROCKET_BASE}/auth/login",
            json={"email": settings.shiprocket_email, "password": settings.shiprocket_password},
        )
        resp.raise_for_status()
        _token = resp.json()["token"]
        _log.info("[shiprocket] authenticated, token refreshed")
        return _token


async def _get_token() -> str:
    global _token
    if _token:
        return _token
    return await _authenticate()


async def _request(method: str, path: str, **kwargs) -> dict:
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(method, f"{_SHIPROCKET_BASE}{path}", headers=headers, **kwargs)
        if resp.status_code == 401:
            _log.warning("[shiprocket] 401 — re-authenticating")
            token = await _authenticate()
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.request(method, f"{_SHIPROCKET_BASE}{path}", headers=headers, **kwargs)
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
