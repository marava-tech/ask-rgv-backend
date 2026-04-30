import logging
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from core.auth import require_user
from db import queries
from db.pool import get_pool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user"])


class UserStatsResponse(BaseModel):
    total_seconds: int
    sessions_count: int
    member_since: str  # ISO date string, e.g. "2026-01-15"


@router.get("/stats", response_model=UserStatsResponse)
async def get_user_stats(user: dict = Depends(require_user)):
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COALESCE(SUM(duration_seconds), 0)::int AS total_seconds,
            COUNT(*)::int AS sessions_count,
            MIN(started_at)::date AS member_since
        FROM sessions
        WHERE user_id = $1
        """,
        UUID(user["sub"]),
    )
    return UserStatsResponse(
        total_seconds=row["total_seconds"] or 0,
        sessions_count=row["sessions_count"] or 0,
        member_since=str(row["member_since"]) if row["member_since"] else str(date.today()),
    )


class UserMemoryResponse(BaseModel):
    summary: str
    key_facts: dict


@router.get("/memory", response_model=UserMemoryResponse)
async def get_user_memory(user: dict = Depends(require_user)):
    user_data = await queries.get_user_by_id(user["sub"])
    if not user_data or user_data.get("tier") != "super_fan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Fan only")
    mem = await queries.get_user_memory(user["sub"])
    if not mem:
        return UserMemoryResponse(summary="", key_facts={})
    return UserMemoryResponse(
        summary=mem.get("summary", ""),
        key_facts=mem.get("key_facts", {}),
    )


@router.delete("/memory", status_code=204)
async def delete_user_memory(user: dict = Depends(require_user)):
    user_data = await queries.get_user_by_id(user["sub"])
    if not user_data or user_data.get("tier") != "super_fan":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super Fan only")
    await queries.delete_user_memory(user["sub"])
