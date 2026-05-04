import logging
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    database_url: str
    redis_url: str
    qdrant_url: str
    qdrant_collection: str = "rgv_knowledge"
    embedding_service_url: str
    ingestion_worker_url: str

    google_client_id: str
    jwt_secret: str
    admin_jwt_secret: str
    admin_password: str = ""  # legacy fallback only — superseded by TOTP when set
    # base32 TOTP secret for Google Authenticator (POST /admin/auth/login uses this)
    admin_totp_secret: str = ""
    # Only this email may obtain an admin dashboard token (POST /admin/auth/login).
    admin_dashboard_email: str = "kinneramadhu123@gmail.com"
    admin_ip_allowlist: str = ""
    youtube_data_api_key: str = ""
    # Dev: set CORS_ALLOWED_ORIGINS=http://localhost:3700,http://localhost:5173 in local .env
    cors_allowed_origins: str = "https://ask-rgv-dashboard.marava.tech"

    anthropic_api_key: str

    google_play_package_name: str = "tech.marava.askrgv"
    google_service_account_json: str = ""  # path to service account JSON file

    deepgram_api_key: str = ""
    smallest_ai_api_key: str = ""
    voice_id_en: str = ""
    voice_id_te: str = ""
    voice_id_hi: str = ""
    tts_speed: float = 0.9

    firebase_service_account_path: str = "/app/firebase-service-account.json"
    app_env: str = "production"

    # Email (waitlist confirmation)
    email_enabled: bool = False
    email_from: str = ""
    email_host: str = "smtp.gmail.com"
    email_port: int = 587
    email_username: str = ""
    email_password: str = ""

    # Merch + payments
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    shiprocket_token: str = ""
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_merch_bucket: str = "merch-images"
    minio_public_url: str = ""
    waitlist_gate_enabled: bool = True

    # Phase 1 latency flags
    rag_rerank_enabled: bool = False   # Haiku rerank OFF by default — saves 300-700 ms/turn
    embed_cache_enabled: bool = True   # Redis embedding cache ON by default
    # Phase 3: pre-warm embed cache on first final transcript so UtteranceEnd→RAG is faster
    speculative_rag_enabled: bool = True
    # Bug #41: workers field removed — Gunicorn reads WORKERS env var directly, not Pydantic settings

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def admin_ip_list(self) -> list[str]:
        return [ip.strip() for ip in self.admin_ip_allowlist.split(",") if ip.strip()]

    # Bug #34: warn at boot when optional secrets are missing so failures aren't silent
    @model_validator(mode="after")
    def warn_missing_secrets(self) -> "Settings":
        if not self.google_service_account_json:
            logger.warning("[config] GOOGLE_SERVICE_ACCOUNT_JSON not set — Play IAP verification disabled")
        if not self.deepgram_api_key:
            logger.warning("[config] DEEPGRAM_API_KEY not set — STT disabled")
        if not self.smallest_ai_api_key:
            logger.warning("[config] SMALLEST_AI_API_KEY not set — TTS disabled")
        if not all([self.voice_id_en, self.voice_id_te, self.voice_id_hi]):
            logger.warning("[config] VOICE_ID_EN/TE/HI not fully set — TTS will use hardcoded fallback IDs")
        if not self.admin_totp_secret:
            logger.warning("[config] ADMIN_TOTP_SECRET not set — dashboard login uses password fallback")
        if not self.youtube_data_api_key:
            logger.warning("[config] YOUTUBE_DATA_API_KEY not set — video title fetch uses yt-dlp fallback")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
