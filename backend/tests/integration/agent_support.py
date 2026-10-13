"""Shared fixtures for investigator tests: no Elasticsearch, no model."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.policy.applicability import applicable_versions_for_domain
from onramp.policy.models import PolicyPassage
from onramp.policy.search import RetrievedPassage


class DatabaseRetriever:
    """Word-overlap ranking over passages in force, read from PostgreSQL."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        words = {w.strip(".,?").lower() for w in query.split() if len(w) > 3}
        with self._sessions() as session:
            versions = {
                (v.policy_id, v.version)
                for v in applicable_versions_for_domain(session, domain, at)
            }
            passages = [
                p
                for p in session.scalars(select(PolicyPassage))
                if (p.policy_id, p.version) in versions
            ]
        scored = sorted(
            passages,
            key=lambda p: (
                -len(words & set(f"{p.heading} {p.text}".lower().split())),
                p.passage_id,
            ),
        )
        return [
            RetrievedPassage(
                p.passage_id, p.policy_id, p.version, p.section, p.heading, p.text, 1.0
            )
            for p in scored[:k]
        ]
