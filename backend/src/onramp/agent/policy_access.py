"""Policy access for the investigator, limited to the versions in force for the case."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.policy.applicability import applicable_versions_for_domain
from onramp.policy.models import PolicyPassage
from onramp.policy.search import RetrievedPassage


def passage_lookup(
    sessions: sessionmaker[Session], domain: str, at: datetime
) -> Callable[[str], RetrievedPassage | None]:
    """A lookup that returns a passage only if it belongs to a version in force at ``at``."""
    with sessions() as session:
        allowed = {
            (v.policy_id, v.version) for v in applicable_versions_for_domain(session, domain, at)
        }

    def lookup(passage_id: str) -> RetrievedPassage | None:
        with sessions() as session:
            p = session.scalar(select(PolicyPassage).filter_by(passage_id=passage_id))
        if p is None or (p.policy_id, p.version) not in allowed:
            return None
        return RetrievedPassage(
            p.passage_id, p.policy_id, p.version, p.section, p.heading, p.text, 1.0
        )

    return lookup
