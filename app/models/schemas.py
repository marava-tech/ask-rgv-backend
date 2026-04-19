from pydantic import BaseModel, Field
from typing import Literal


# ── Auth ──────────────────────────────────────────────────────────────────────

class GoogleAuthRequest(BaseModel):
    id_token: str


class UserInfo(BaseModel):
    id: str
    email: str | None = None
    name: str | None = None
    avatar_url: str | None = None
    tier: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserInfo | None = None


class RefreshRequest(BaseModel):
    refresh_token: str
    # Bug #17: optionally accept current access token so it can be blacklisted on refresh
    access_token: str | None = None


class AdminLoginRequest(BaseModel):
    password: str


# Bug #22: accept refresh_token to scope logout to current device only
class LogoutRequest(BaseModel):
    refresh_token: str | None = None


# ── Conversation ──────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    device_id: str | None = None
    mode: str = "default"


class SessionData(BaseModel):
    id: str
    mode: str = "default"
    # Bug #39: language removed — it is "en" by default but unknown until first turn;
    # the field was misleading; clients should read it from the turn_id event instead
    started_at: str


class StartSessionResponse(BaseModel):
    session: SessionData


class TurnRequest(BaseModel):
    session_id: str
    message: str
    mode: Literal["default", "hard_truth"] = "default"
    device_id: str | None = None


class UsageRequest(BaseModel):
    session_id: str
    turn_id: str | None = None
    played_seconds: int
    device_id: str | None = None


class InterruptRequest(BaseModel):
    session_id: str
    active_turn_id: str
    played_seconds: int
    device_id: str | None = None


class EndSessionRequest(BaseModel):
    session_id: str


# ── Subscription ──────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    tier: Literal["fan", "super_fan"]


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str = "INR"


class WebhookPayload(BaseModel):
    event: str
    payload: dict


# ── Admin ingestion ───────────────────────────────────────────────────────────

class IngestSingleRequest(BaseModel):
    url: str
    language: Literal["en", "te", "hi"]


class IngestBulkRequest(BaseModel):
    urls: list[str]
    language: Literal["en", "te", "hi"]


class ToggleRequest(BaseModel):
    enabled: bool
