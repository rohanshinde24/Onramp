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

    # Required only by process roles that produce or consume events.
    kafka_bootstrap_servers: str | None = None
    kafka_client_id: str = "onramp"
    # Bounds how long the outbox publisher holds row locks waiting for broker acks.
    kafka_delivery_timeout_ms: int = Field(default=15_000, ge=1_000, le=120_000)
    kafka_topic_partitions: int = Field(default=3, ge=1)
    kafka_replication_factor: int = Field(default=1, ge=1)

    # Required only by roles that index or search policy passages.
    elasticsearch_url: str | None = None
    policy_index_alias: str = "policy-passages"

    # Required only by workflow and agent worker roles.
    temporal_address: str = "127.0.0.1:7233"
    temporal_namespace: str = "default"
    temporal_workflow_task_queue: str = "onramp-workflows"
    temporal_agent_task_queue: str = "onramp-agent"

    otel_exporter_otlp_endpoint: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
