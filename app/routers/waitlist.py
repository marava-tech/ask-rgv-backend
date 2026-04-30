from typing import Literal

import asyncpg
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel, EmailStr

from db.pool import get_pool
from services.email import send_waitlist_confirmation
from services.promo import generate_app_code, generate_merch_code

router = APIRouter(prefix="/waitlist", tags=["waitlist"])


class WaitlistJoinRequest(BaseModel):
    name: str
    email: EmailStr
    language: str
    is_rgv_fan: str | None = None
    merch_interest: list[Literal["tshirt", "hoodie", "poster", "mug", "not_sure"]] | None = None


class WaitlistJoinResponse(BaseModel):
    app_promo_code: str
    merch_promo_code: str


@router.post("/join", response_model=WaitlistJoinResponse, status_code=status.HTTP_201_CREATED)
async def join_waitlist(body: WaitlistJoinRequest, background_tasks: BackgroundTasks):
    pool = get_pool()

    app_code = await generate_app_code()
    merch_code = await generate_merch_code()

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO waitlist_signups (name, email, language, is_rgv_fan, app_promo_code, merch_promo_code)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id
            """,
            body.name,
            str(body.email).lower(),
            body.language,
            body.is_rgv_fan,
            app_code,
            merch_code,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already_registered")

    if body.merch_interest:
        await pool.execute(
            "INSERT INTO waitlist_merch_interest (waitlist_id, categories) VALUES ($1, $2)",
            row["id"],
            body.merch_interest,
        )

    background_tasks.add_task(
        send_waitlist_confirmation,
        body.name,
        str(body.email).lower(),
        app_code,
        merch_code,
    )

    return WaitlistJoinResponse(app_promo_code=app_code, merch_promo_code=merch_code)
