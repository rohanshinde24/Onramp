import os

import pytest

# Integration tests read database URLs from the environment. Locally these default to the
# compose PostgreSQL; CI sets them explicitly.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://onramp:onramp-local@localhost:5432/onramp"
)
os.environ.setdefault(
    "MARKETPLACE_DATABASE_URL",
    "postgresql+psycopg://onramp:onramp-local@localhost:5432/marketplace",
)

os.environ.setdefault("AUTH_JWT_SECRET", "test-only-signing-key-not-a-secret-0123456789")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        path = str(item.path)
        if "tests/integration/" in path:
            item.add_marker(pytest.mark.integration)
        if "tests/kafka/" in path:
            item.add_marker(pytest.mark.kafka)
