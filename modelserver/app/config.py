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

    minio_url: str = Field(default="http://localhost:9000")
    minio_root_user: str = Field(default="minioadmin")
    minio_root_password: str = Field(default="minioadmin")
    minio_bucket: str = Field(default="maintainers-copilot")

    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
