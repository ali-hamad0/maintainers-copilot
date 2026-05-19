from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
    )

    modelserver_host: str = Field(default="0.0.0.0")
    modelserver_port: int = Field(default=8001)
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    # ── Vault (passed by docker-compose; not used directly but must be declared) ─
    vault_addr: str = Field(default="http://vault:8200")
    vault_root_token: str = Field(default="dev-root-token")

    # ── MinIO (creds are non-secret in dev; swap for Vault-sourced in prod) ─────
    minio_endpoint: str = Field(default="minio:9000")
    minio_access_key: str = Field(default="minioadmin")
    minio_secret_key: str = Field(default="minioadmin123")
    minio_bucket: str = Field(default="maintainers-copilot")

    # ── Classifier weights ───────────────────────────────────────────────────────
    # MinIO object key for the weights file.
    weights_minio_key: str = Field(default="models/classifier/weights.pt")
    # Expected SHA-256 of weights.pt.  Empty string = skip SHA-256 verification
    # (only safe before training is complete).
    weights_sha256: str = Field(default="")

    # ── Gemini (for summarise endpoint) ─────────────────────────────────────────
    # Empty string = API key not configured; summarise endpoint returns fallback.
    gemini_api_key: str = Field(default="")

    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
