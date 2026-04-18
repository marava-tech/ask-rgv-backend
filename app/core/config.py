from pydantic_settings import BaseSettings, SettingsConfigDict


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
    cors_allowed_origins: str = "https://ask-rgv-dashboard.marava.tech"

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
    workers: int = 4

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def admin_ip_list(self) -> list[str]:
        return [ip.strip() for ip in self.admin_ip_allowlist.split(",") if ip.strip()]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
