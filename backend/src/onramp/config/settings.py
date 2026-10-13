"""Typed application settings read from the environment.

Only settings that some code path uses live here. Kafka, Elasticsearch, Temporal and model
provider settings are added by the phases that introduce those dependencies.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    TEST = "test"
    CI = "ci"
    STAGING = "staging"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    app_env: AppEnv = AppEnv.LOCAL
    service_name: str = "onramp"
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR)$")

    database_url: PostgresDsn
    marketplace_database_url: PostgresDsn

    # HS256 signing key for API bearer tokens. Local and CI only; a deployed environment would
    # verify tokens from an identity provider instead.
    auth_jwt_secret: SecretStr = Field(min_length=32)
    auth_issuer: str = "onramp"
    auth_audience: str = "onramp-api"

    otel_exporter_otlp_endpoint: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
