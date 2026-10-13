import pytest
from pydantic import ValidationError

from onramp.config import AppEnv, Settings

DB = "postgresql+psycopg://onramp:onramp@localhost:5432/onramp"
MP = "postgresql+psycopg://onramp:onramp@localhost:5432/marketplace"
KEY = "k" * 32


def test_reads_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("MARKETPLACE_DATABASE_URL", MP)
    monkeypatch.setenv("AUTH_JWT_SECRET", KEY)

    settings = Settings(_env_file=None)

    assert settings.app_env is AppEnv.TEST
    assert str(settings.database_url) == DB
    assert settings.otel_exporter_otlp_endpoint is None


def test_missing_database_url_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("MARKETPLACE_DATABASE_URL", MP)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_rejects_unknown_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("MARKETPLACE_DATABASE_URL", MP)

    with pytest.raises(ValidationError, match="app_env"):
        Settings(_env_file=None)


def test_short_signing_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB)
    monkeypatch.setenv("MARKETPLACE_DATABASE_URL", MP)
    monkeypatch.setenv("AUTH_JWT_SECRET", "short")

    with pytest.raises(ValidationError, match="auth_jwt_secret"):
        Settings(_env_file=None)
