"""Loading the corpus into the database. Loaded versions are immutable."""

from dataclasses import dataclass

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


def load_documents(session: Session, documents: list[PolicyDocument]) -> LoadResult:
    loaded, unchanged = [], []
    for doc in documents:
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
            if existing.source_sha256 != doc.sha256:
                raise ImmutableVersionError(f"{label} changed after it was loaded")
            unchanged.append(label)
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
    return LoadResult(loaded, unchanged)
