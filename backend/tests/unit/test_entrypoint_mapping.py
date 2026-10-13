"""Each process role must be able to configure its ORM mappers on its own.

Tests import every model up front, which hid a production failure: a role whose imports did not
reach a table referenced by a foreign key failed at its first query. Each check runs in a clean
interpreter, importing only the entrypoint module, as the container does.
"""

import subprocess
import sys

import pytest

ENTRYPOINTS = [
    "onramp.entrypoints.api",
    "onramp.entrypoints.event_consumer",
    "onramp.entrypoints.outbox_publisher",
    "onramp.entrypoints.seed",
    "onramp.entrypoints.migrate",
    "onramp.entrypoints.dlq_replay",
    "onramp.entrypoints.load_policies",
]


@pytest.mark.parametrize("module", ENTRYPOINTS)
def test_entrypoint_resolves_every_foreign_key(module: str) -> None:
    # Foreign key targets resolve lazily, at the first flush; resolve them all now.
    code = (
        f"import {module}\n"
        "from onramp.db.base import OnrampBase\n"
        "for table in OnrampBase.metadata.tables.values():\n"
        "    for fk in table.foreign_keys:\n"
        "        fk.column\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and module list
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=60
    )

    assert result.returncode == 0, result.stderr[-2000:]
