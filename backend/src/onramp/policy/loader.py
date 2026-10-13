"""Loading the corpus into the database. Loaded versions are immutable.

The one change a loaded version accepts is being closed: an open version (no end date) may be
given an end date when a newer version takes over, provided nothing else in its source changed
and the end date is not in the past. Ending a version retroactively would change which rules
governed incidents that were already decided.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.policy.corpus import PolicyDocument
from onramp.policy.models import Policy, PolicyPassage, PolicyVersion


class ImmutableVersionError(ValueError):
    """A loaded policy version's source changed. Publish a new version instead."""


@dataclass(frozen=True)
class LoadResult:
    loaded: list[str]
    unchanged: list[str]
    closed: list[str] = field(default_factory=list)


def load_documents(
    session: Session, documents: list[PolicyDocument], *, now: datetime | None = None
) -> LoadResult:
    loaded, unchanged, closed = [], [], []
    # Older versions first, so a version is closed before its successor's period is added.
    for doc in sorted(documents, key=lambda d: (d.front.policy_id, d.front.version)):
        front = doc.front
        label = f"{front.policy_id} v{front.version}"
        policy = session.get(Policy, front.policy_id)
        if policy is None:
            session.add(Policy(id=front.policy_id, domain=front.domain, title=front.title))
            session.flush()
        elif policy.domain != front.domain:
            raise ImmutableVersionError(f"{front.policy_id}: domain changed")

        existing = session.scalar(
            select(PolicyVersion).filter_by(policy_id=front.policy_id, version=front.version)
        )
        if existing is not None:
            if existing.source_sha256 == doc.sha256:
                unchanged.append(label)
            else:
                _close(session, existing, doc, now or datetime.now(UTC), label)
                closed.append(label)
            continue

        session.add(
            PolicyVersion(
                policy_id=front.policy_id,
                version=front.version,
                effective_from=front.effective_from,
                effective_until=front.effective_until,
                parameters=doc.parameters,
                source_sha256=doc.sha256,
            )
        )
        session.flush()
        session.add_all(
            PolicyPassage(
                passage_id=p.passage_id,
                policy_id=front.policy_id,
                version=front.version,
                section=p.section,
                heading=p.heading,
                text=p.text,
            )
            for p in doc.passages
        )
        session.flush()
        loaded.append(label)
    return LoadResult(loaded, unchanged, closed)


def _close(
    session: Session, existing: PolicyVersion, doc: PolicyDocument, now: datetime, label: str
) -> None:
    front = doc.front
    stored = {
        (p.passage_id, p.section, p.heading, p.text)
        for p in session.scalars(
            select(PolicyPassage).filter_by(policy_id=front.policy_id, version=front.version)
        )
    }
    only_closing = (
        existing.effective_until is None
        and front.effective_until is not None
        and front.effective_from == existing.effective_from
        and doc.parameters == existing.parameters
        and {(p.passage_id, p.section, p.heading, p.text) for p in doc.passages} == stored
    )
    if not only_closing:
        raise ImmutableVersionError(f"{label} changed after it was loaded")
    if front.effective_until is not None and front.effective_until < now:
        raise ImmutableVersionError(f"{label} cannot be ended in the past")
    existing.effective_until = front.effective_until
    existing.source_sha256 = doc.sha256
    session.flush()
