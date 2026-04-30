import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from core.config import settings
from db import queries
from models.schemas import MerchOrderUpdateRequest, MerchProductCreate, MerchProductUpdate
from services import shiprocket as shiprocket_service

_log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/merch", tags=["admin-merch"])


# ── Products ──────────────────────────────────────────────────────────────────

@router.get("/products")
async def admin_list_products(_=Depends(require_admin)):
    products = await queries.list_merch_products(enabled_only=False)
    return {"products": products}


@router.post("/products", status_code=201)
async def admin_create_product(body: MerchProductCreate, _=Depends(require_admin)):
    variants = [v.model_dump() for v in body.variants]
    for v in variants:
        if not v.get("id"):
            v["id"] = str(uuid.uuid4())
    product = await queries.create_merch_product(
        name=body.name,
        description=body.description,
        price_inr=body.price_inr,
        image_url=body.image_url,
        variants=variants,
    )
    return product


@router.put("/products/{product_id}")
async def admin_update_product(product_id: str, body: MerchProductUpdate, _=Depends(require_admin)):
    updates = body.model_dump(exclude_none=True)
    if "variants" in updates:
        for v in updates["variants"]:
            if not v.get("id"):
                v["id"] = str(uuid.uuid4())
    product = await queries.update_merch_product(product_id, updates)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.delete("/products/{product_id}", status_code=204)
async def admin_delete_product(product_id: str, _=Depends(require_admin)):
    product = await queries.update_merch_product(product_id, {"enabled": False})
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")


@router.get("/products/image-upload-url")
async def admin_image_upload_url(filename: str, _=Depends(require_admin)):
    if not settings.minio_endpoint:
        raise HTTPException(status_code=503, detail="MinIO not configured")
    try:
        from minio import Minio
        from minio.error import S3Error
        import urllib.parse

        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=not settings.minio_endpoint.startswith("localhost"),
        )
        bucket = settings.minio_merch_bucket
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
                policy = f'{{"Version":"2012-10-17","Statement":[{{"Effect":"Allow","Principal":{{"AWS":["*"]}},"Action":["s3:GetObject"],"Resource":["arn:aws:s3:::{bucket}/*"]}}]}}'
                client.set_bucket_policy(bucket, policy)
        except S3Error as e:
            _log.warning("[admin_merch] bucket setup: %s", e)

        object_name = f"products/{uuid.uuid4()}-{filename}"
        upload_url = client.presigned_put_object(bucket, object_name, expires=__import__("datetime").timedelta(minutes=30))

        base = settings.minio_public_url or f"http://{settings.minio_endpoint}"
        object_url = f"{base}/{bucket}/{object_name}"
        return {"upload_url": upload_url, "object_url": object_url}
    except ImportError:
        raise HTTPException(status_code=503, detail="MinIO client not installed")


# ── Orders ────────────────────────────────────────────────────────────────────

@router.get("/orders")
async def admin_list_orders(_=Depends(require_admin)):
    orders = await queries.list_merch_orders()
    return {"orders": orders}


@router.patch("/orders/{order_id}")
async def admin_update_order(order_id: str, body: MerchOrderUpdateRequest, _=Depends(require_admin)):
    order = await queries.get_merch_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if body.status:
        await queries.update_merch_order_status(order_id, body.status)
    return await queries.get_merch_order(order_id)


@router.post("/orders/{order_id}/fulfill")
async def admin_fulfill_order(order_id: str, _=Depends(require_admin)):
    order = await queries.get_merch_order(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order["status"] != "paid":
        raise HTTPException(status_code=400, detail="Order must be in 'paid' status to fulfill")

    product = await queries.get_merch_product(order["product_id"])
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    try:
        result = await shiprocket_service.create_shipment(order, product)
    except Exception as e:
        _log.error("[admin_merch] Shiprocket fulfillment failed for order %s: %s", order_id, e)
        raise HTTPException(status_code=502, detail=f"Shiprocket error: {e}")

    shiprocket_order_id = str(result.get("order_id", ""))
    shiprocket_awb = str(result.get("awb_code", ""))

    await queries.fulfill_merch_order(
        order_id=order_id,
        shiprocket_order_id=shiprocket_order_id,
        shiprocket_awb=shiprocket_awb,
    )

    return {"order_id": order_id, "status": "fulfilled", "awb": shiprocket_awb}
