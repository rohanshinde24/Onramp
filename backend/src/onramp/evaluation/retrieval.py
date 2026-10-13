"""Retrieval benchmark: does search return the right passage from the right version?

Metrics are computed deterministically from passage IDs. No model judges relevance.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

import yaml
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from onramp.policy.applicability import applicable_version
from onramp.policy.models import Policy, PolicyPassage
from onramp.policy.search import PolicyRetriever

K_VALUES = (1, 3, 5)
DEPTH = 10


class LabeledQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    domain: str
    at: AwareDatetime
    query: str = Field(min_length=3)
    relevant: tuple[str, ...] = Field(min_length=1)


class QuerySet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    queries: list[LabeledQuery]


class LabelError(ValueError):
    """A label cannot be right: unknown passage, or a passage not in force at the query time."""


def load_queries(path: Path) -> QuerySet:
    query_set = QuerySet.model_validate(yaml.safe_load(path.read_text()))
    ids = [q.id for q in query_set.queries]
    if len(set(ids)) != len(ids):
        raise LabelError("duplicate query IDs")
    return query_set


def in_force_versions(session: Session, domain: str, at: datetime) -> set[tuple[str, int]]:
    policies = session.scalars(select(Policy.id).filter_by(domain=domain))
    return {(p, applicable_version(session, p, at).version) for p in policies}


def validate_labels(session: Session, query_set: QuerySet) -> None:
    """Every relevant passage must exist and belong to a version in force at the query time.
    A benchmark with a wrong label would quietly reward the wrong behavior."""
    for q in query_set.queries:
        allowed = in_force_versions(session, q.domain, q.at)
        for passage_id in q.relevant:
            passage = session.get(PolicyPassage, passage_id)
            if passage is None:
                raise LabelError(f"{q.id}: unknown passage {passage_id}")
            if (passage.policy_id, passage.version) not in allowed:
                raise LabelError(f"{q.id}: {passage_id} is not in force at {q.at.isoformat()}")


def recall_at(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> float:
    return len(set(retrieved[:k]) & set(relevant)) / len(set(relevant))


def reciprocal_rank(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    for rank, passage_id in enumerate(retrieved, start=1):
        if passage_id in relevant:
            return 1.0 / rank
    return 0.0


def stale_fraction(
    retrieved_versions: Sequence[tuple[str, int]], in_force: set[tuple[str, int]]
) -> float:
    """Share of returned passages from a version not in force at the incident time."""
    if not retrieved_versions:
        return 0.0
    return sum(1 for v in retrieved_versions if v not in in_force) / len(retrieved_versions)


@dataclass(frozen=True)
class QueryResult:
    method: str
    query_id: str
    domain: str
    retrieved: list[str]
    recall: dict[str, float]
    reciprocal_rank: float
    stale_fraction: float
    top1_stale: bool


def evaluate(
    session: Session, query_set: QuerySet, method: str, retriever: PolicyRetriever
) -> list[QueryResult]:
    results = []
    for q in query_set.queries:
        hits = retriever.search(q.domain, q.at, q.query, k=DEPTH)
        ids = [h.passage_id for h in hits]
        versions = [(h.policy_id, h.version) for h in hits]
        in_force = in_force_versions(session, q.domain, q.at)
        results.append(
            QueryResult(
                method=method,
                query_id=q.id,
                domain=q.domain,
                retrieved=ids,
                recall={f"recall@{k}": recall_at(ids, q.relevant, k) for k in K_VALUES},
                reciprocal_rank=reciprocal_rank(ids, q.relevant),
                stale_fraction=stale_fraction(versions, in_force),
                top1_stale=bool(versions) and versions[0] not in in_force,
            )
        )
    return results


def summarize(results: Sequence[QueryResult]) -> dict[str, Any]:
    summary: dict[str, Any] = {"queries": len(results)}
    for k in K_VALUES:
        summary[f"recall@{k}"] = mean(r.recall[f"recall@{k}"] for r in results)
    summary["mrr@10"] = mean(r.reciprocal_rank for r in results)
    summary["stale_fraction@10"] = mean(r.stale_fraction for r in results)
    summary["top1_stale_rate"] = mean(1.0 if r.top1_stale else 0.0 for r in results)
    return summary


def to_record(result: QueryResult) -> dict[str, Any]:
    return asdict(result)
