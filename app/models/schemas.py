from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
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
    preferred_language: Literal["te", "hi", "en"] | None = None
    preferred_name: str | None = None
    current_streak: int = 0
    longest_streak: int = 0
    last_active_date: date | None = None
    unlocked_themes: list[str] = Field(default_factory=lambda: ["default"])

class GamificationActivityResponse(BaseModel):
    current_streak: int
    longest_streak: int
    streak_updated: bool

class ThemeUnlockRequest(BaseModel):
    theme_id: str


class UpdateMeRequest(BaseModel):
    preferred_language: Literal["te", "hi", "en"] | None = None
    preferred_name: str | None = Field(default=None, max_length=50)


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


class DeviceTokenRequest(BaseModel):
    device_id: str


class DeviceTokenResponse(BaseModel):
    device_id: str
    device_token: str


# ── Conversation ──────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    device_id: str | None = None
    device_token: str | None = None
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
    device_token: str | None = None
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
    device_token: str | None = None


class InterruptRequest(BaseModel):
    session_id: str
    active_turn_id: str
    played_seconds: int = Field(ge=0, le=600)
    device_id: str | None = None
    device_token: str | None = None


class EndSessionRequest(BaseModel):
    session_id: str


# ── Subscription ──────────────────────────────────────────────────────────────

class IAPVerifyRequest(BaseModel):
    product_id: str
    purchase_token: str


class IAPVerifyResponse(BaseModel):
    tier: str
    success: bool


class PackVerifyRequest(BaseModel):
    product_id: Literal["ask_rgv_pack_5", "ask_rgv_pack_12"]
    purchase_token: str


class PackVerifyResponse(BaseModel):
    seconds_credited: int
    success: bool
    turns_credited: int = 0  # deprecated, kept for backward compat


# ── Quiz ─────────────────────────────────────────────────────────────────────

class QuizQuestionOut(BaseModel):
    id: UUID
    question_text: str
    options: list[str]
    allows_multiple_select: bool
    display_order: int


class QuizAnswerIn(BaseModel):
    question_id: UUID
    selected_option_indices: list[int] = Field(min_length=1)


class QuizSubmitRequest(BaseModel):
    answers: list[QuizAnswerIn]

    @field_validator("answers")
    @classmethod
    def must_be_11(cls, v: list) -> list:
        if len(v) != 11:
            raise ValueError("exactly 11 answers required")
        return v


class QuizSubmitResponse(BaseModel):
    session_id: str
    status: str


class QuizAssessmentOut(BaseModel):
    status: str
    assessment_text: str | None = None
    assessed_at: datetime | None = None
    expires_at: datetime | None = None
    version: int | None = None
    can_reassess: bool = True


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


# ── Bug reports ───────────────────────────────────────────────────────────────

class BugReportRequest(BaseModel):
    description: str = Field(..., min_length=10, max_length=2000)
    screenshot_url: str | None = Field(default=None, max_length=500)
    device_info: dict | None = None


class BugReportUpdateRequest(BaseModel):
    status: Literal["open", "in_progress", "resolved", "wont_fix"] | None = None
    admin_notes: str | None = Field(default=None, max_length=2000)


# ── Merch ─────────────────────────────────────────────────────────────────────

class PromoValidateResponse(BaseModel):
    valid: bool
    discount_percent: int | None = None
    discount_amount_inr: int | None = None
    error_type: str | None = None


class MerchVariant(BaseModel):
    id: str
    label: str
    sku: str


class MerchProductCreate(BaseModel):
    name: str
    description: str | None = None
    price_inr: int
    image_url: str | None = None
    variants: list[MerchVariant] = []


class MerchProductUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price_inr: int | None = None
    image_url: str | None = None
    variants: list[MerchVariant] | None = None
    enabled: bool | None = None
    presale_start_at: datetime | None = None
    presale_end_at: datetime | None = None
    presale_discount_pct: int | None = None
    is_out_of_stock: bool | None = None


class MerchProductOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    price_inr: int
    effective_price_inr: int
    is_presale_active: bool = False
    presale_price_inr: int | None = None
    presale_ends_at: datetime | None = None
    presale_discount_pct: int | None = None
    image_url: str | None = None
    variants: list[MerchVariant] = []
    enabled: bool
    recent_bought_count: int = 0
    is_out_of_stock: bool = False


class ShippingAddress(BaseModel):
    name: str
    phone: str
    address_line1: str
    city: str
    state: str
    pincode: str
    country: str = "India"


class InitiateOrderRequest(BaseModel):
    product_id: str
    variant_id: str
    promo_code: str | None = None


class InitiateOrderResponse(BaseModel):
    order_id: str
    razorpay_order_id: str
    amount: int
    key_id: str


class ConfirmOrderRequest(BaseModel):
    order_id: str
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    shipping_address: ShippingAddress


class MerchOrderOut(BaseModel):
    id: str
    user_id: str
    product_id: str
    variant_id: str
    amount_inr: int
    original_amount_inr: int
    promo_code: str | None = None
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    status: str
    shipping_address: dict | None = None
    shiprocket_order_id: str | None = None
    shiprocket_awb: str | None = None
    shiprocket_status: str | None = None


class MerchOrderUpdateRequest(BaseModel):
    status: Literal["pending", "paid", "fulfilled", "delivered", "cancelled"] | None = None
