from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Non-secret application settings.  Secrets (DB password, API keys, JWT key,
    MinIO creds) live in Vault and are loaded at startup by app/infra/vault.py.
    .env must contain ONLY the Vault root token and non-secret tunables.
    extra="forbid" ensures a typo in .env fails loudly at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
    )

    # ── Vault ────────────────────────────────────────────────────────────────
    vault_root_token: str = Field(default="dev-root-token")
    vault_addr: str = Field(default="http://vault:8200")

    # ── Database (non-secret parts; password comes from Vault) ───────────────
    db_host: str = Field(default="db")
    db_port: int = Field(default=5432)
    db_user: str = Field(default="postgres")
    db_name: str = Field(default="maintainers_copilot")

    # ── Redis (no auth in dev) ────────────────────────────────────────────────
    redis_url: str = Field(default="redis://redis:6379/0")

    # ── MinIO (non-secret endpoint; creds come from Vault) ───────────────────
    minio_endpoint: str = Field(default="minio:9000")
    minio_bucket: str = Field(default="maintainers-copilot")

    # ── Service URLs ─────────────────────────────────────────────────────────
    modelserver_base_url: str = Field(default="http://modelserver:8001")

    # ── Application ──────────────────────────────────────────────────────────
    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # ── LLM / ML ─────────────────────────────────────────────────────────────
    llm_provider: str = Field(default="gemini")
    embedding_model: str = Field(default="gemini-embedding-001")
    embedding_dim: int = Field(default=768)

    # ── Tracing ───────────────────────────────────────────────────────────────
    tracing_backend: str = Field(default="langsmith")
    langsmith_project: str = Field(default="maintainers-copilot")

    @property
    def db_url_async(self) -> str:
        """Constructed at runtime; password injected from Vault secrets."""
        raise RuntimeError("Use app.state.db_url (set by lifespan after Vault read) instead.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
