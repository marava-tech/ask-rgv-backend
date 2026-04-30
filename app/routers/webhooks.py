import logging

from fastapi import APIRouter, Request

from db import queries

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SHIPROCKET_STATUS_MAP = {
    "Delivered": "delivered",
    "Cancelled": "cancelled",
    "RTO Delivered": "cancelled",
}


@router.post("/shiprocket")
async def shiprocket_webhook(request: Request):
    try:
        payload = await request.json()
    except Exception:
        return {"ok": True}

    _log.info("[webhook/shiprocket] payload: %s", str(payload)[:500])

    shiprocket_order_id = str(payload.get("order_id", ""))
    current_status = str(payload.get("current_status", ""))

    if not shiprocket_order_id:
        return {"ok": True}

    order = await queries.get_merch_order_by_shiprocket_id(shiprocket_order_id)
    if not order:
        _log.info("[webhook/shiprocket] unknown shiprocket_order_id %s — ignoring", shiprocket_order_id)
        return {"ok": True}

    our_status = _SHIPROCKET_STATUS_MAP.get(current_status)
    await queries.update_merch_order_by_shiprocket_id(
        shiprocket_order_id=shiprocket_order_id,
        status=our_status or order.get("status", "fulfilled"),
        shiprocket_status=current_status,
    )

    return {"ok": True}
