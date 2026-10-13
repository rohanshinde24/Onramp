"""CHECK constraints are invisible to the migration drift test, so cover this one directly."""

import re

from sqlalchemy import text
from sqlalchemy.engine import Engine


def test_database_accepts_exactly_the_known_proposer_types(onramp_engine: Engine) -> None:
    with onramp_engine.connect() as connection:
        definition = connection.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_proposal_known_proposer_type'"
            )
        ).scalar_one()

    assert set(re.findall(r"'(\w+)'", definition)) == {"user", "agent", "system"}
