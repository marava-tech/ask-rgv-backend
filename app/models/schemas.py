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
    email: str
    totp_code: str  # 6-digit TOTP from Google Authenticator


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
    mode: Literal["default", "hard_truth", "argue"] = "default"
    device_id: str | None = None
    source: Literal["text", "voice"] = "text"
    # STT-detected language hint — takes priority over text-based detection for voice turns
    hint_language: Literal["en", "te", "hi"] | None = None


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class UsageRequest(BaseModel):
    session_id: str
    turn_id: str | None = None
    played_seconds: int = Field(ge=0, le=600)
    device_id: str | None = None


class InterruptRequest(BaseModel):
    session_id: str
    active_turn_id: str
    played_seconds: int = Field(ge=0, le=600)
    device_id: str | None = None


class EndSessionRequest(BaseModel):
    session_id: str


# ── Subscription ──────────────────────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    tier: Literal["fan", "super_fan"]
    is_upgrade: bool = False


class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int
    currency: str = "INR"
    key_id: str


class UpgradePriceResponse(BaseModel):
    prorated_amount_paise: int
    prorated_amount_rupees: int
    remaining_days: int
    period_end: str
    fan_price_rupees: int = 99
    super_fan_price_rupees: int = 299
    discount_rupees: int


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


# ── Admin quotes (quote-of-day pool) ──────────────────────────────────────────


class QuoteCreateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)
    source: str | None = Field(default=None, max_length=2000)
    language: Literal["en", "te", "hi"] = "en"
