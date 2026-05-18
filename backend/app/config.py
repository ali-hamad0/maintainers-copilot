from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/maintainers_copilot"
    )
    redis_url: str = Field(default="redis://localhost:6379/0")
    vault_addr: str = Field(default="http://localhost:8200")
    vault_root_token: str = Field(default="")

    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    environment: str = Field(default="development")
    debug: bool = Field(default=False)

    jwt_secret_key: str = Field(default="your-secret-key")
    jwt_algorithm: str = Field(default="HS256")
    jwt_expiration_minutes: int = Field(default=30)

    llm_provider: str = Field(default="gemini")
    gemini_api_key: str = Field(default="")

    embedding_model: str = Field(default="gemini-embedding-001")
    embedding_dim: int = Field(default=768)

    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
