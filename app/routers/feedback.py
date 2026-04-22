import logging
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from db import queries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/feedback", tags=["feedback"])


class BugReportRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=2000)
    screenshot_url: str | None = None
    device_info: dict | None = None
    user_id: str | None = None


@router.post("/bug", status_code=201)
async def report_bug(body: BugReportRequest):
    await queries.insert_bug_report(
        user_id=body.user_id,
        description=body.description,
        screenshot_url=body.screenshot_url,
        device_info=body.device_info,
    )
    return {"ok": True}
