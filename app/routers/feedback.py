import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.auth import get_current_user
from db import queries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])

_MAX_DEVICE_INFO_KEYS = 20


class BugReportRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=2000)
    screenshot_url: str | None = Field(default=None, max_length=500)
    device_info: dict | None = None


@router.post("/bug", status_code=201)
async def report_bug(
    body: BugReportRequest,
    user: dict | None = Depends(get_current_user),
):
    user_id = user["sub"] if user else None

    # Cap device_info to prevent payload-bomb: only first N keys, string values only
    safe_device_info: dict | None = None
    if body.device_info:
        safe_device_info = {
            k: str(v)[:200]
            for k, v in list(body.device_info.items())[:_MAX_DEVICE_INFO_KEYS]
            if isinstance(k, str)
        }

    await queries.insert_bug_report(
        user_id=user_id,
        description=body.description,
        screenshot_url=body.screenshot_url,
        device_info=safe_device_info,
    )
    return {"ok": True}
