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
    admin_password: str
    admin_ip_allowlist: str = ""
    # Bug #35: default includes localhost dev origin so local dashboard works without env var
    cors_allowed_origins: str = "https://ask-rgv-dashboard.marava.tech,http://localhost:3700,http://localhost:5173"

    anthropic_api_key: str

    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    deepgram_api_key: str = ""
    smallest_ai_api_key: str = ""
    smallest_ai_voice_en: str = ""
    smallest_ai_voice_te: str = ""
    smallest_ai_voice_hi: str = ""

    firebase_service_account_path: str = "/app/firebase-service-account.json"
    app_env: str = "production"
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
        if not self.razorpay_key_id or not self.razorpay_key_secret:
            logger.warning("[config] RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET not set — payments disabled")
        if not self.razorpay_webhook_secret:
            logger.warning("[config] RAZORPAY_WEBHOOK_SECRET not set — webhook endpoint will reject all events")
        if not self.deepgram_api_key:
            logger.warning("[config] DEEPGRAM_API_KEY not set — STT disabled")
        if not self.smallest_ai_api_key:
            logger.warning("[config] SMALLEST_AI_API_KEY not set — TTS disabled")
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
