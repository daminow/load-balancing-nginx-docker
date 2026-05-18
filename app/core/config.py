from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, SecretStr, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "load-balancing-app"
    app_env: Literal["development", "production", "test"] = "production"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=8)
    postgres_db: str = Field(min_length=1)
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)

    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    db_pool_timeout: int = Field(default=30, ge=1, le=300)

    database_url: PostgresDsn | None = None

    cors_allow_origins: list[str] = Field(default_factory=list)

    instance_id: str = "unknown"

    @field_validator("database_url", mode="before")
    @classmethod
    def assemble_database_url(cls, v: str | None, info: ValidationInfo) -> str:
        if isinstance(v, str) and v:
            return v
        data = info.data
        password = data["postgres_password"]
        secret = password.get_secret_value() if isinstance(password, SecretStr) else password
        return (
            f"postgresql+asyncpg://{data['postgres_user']}:{secret}"
            f"@{data['postgres_host']}:{data['postgres_port']}/{data['postgres_db']}"
        )

    @property
    def sync_database_url(self) -> str:
        url = str(self.database_url)
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
