from pathlib import Path

import pytest

from onramp.db import migrate


def test_source_tree_alembic_ini_is_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ONRAMP_ALEMBIC_INI", raising=False)

    assert migrate.alembic_ini().is_file()


def test_environment_overrides_alembic_ini(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONRAMP_ALEMBIC_INI", "/home/onramp/alembic.ini")

    assert migrate.alembic_ini() == Path("/home/onramp/alembic.ini")


def test_unknown_chain_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown migration chain"):
        migrate.alembic_config("billing", "postgresql://x/y")
