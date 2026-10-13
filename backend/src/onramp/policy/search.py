"""Policy passage search (ADR-003).

The index is a rebuildable projection of PostgreSQL. Every query filters to the case's domain
and to versions in force at the incident time before any ranking, so a passage from the wrong
version cannot be returned however relevant its text is.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from elasticsearch import Elasticsearch, helpers
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.policy.models import Policy, PolicyPassage, PolicyVersion

MAPPING: dict[str, Any] = {
    "dynamic": "strict",
    "properties": {
        "passage_id": {"type": "keyword"},
        "policy_id": {"type": "keyword"},
        "domain": {"type": "keyword"},
        "version": {"type": "integer"},
        "section": {"type": "keyword"},
        "heading": {"type": "text", "analyzer": "english"},
        "text": {"type": "text", "analyzer": "english"},
        "effective_from": {"type": "date"},
        "effective_until": {"type": "date"},
    },
}


@dataclass(frozen=True)
class RetrievedPassage:
    passage_id: str
    policy_id: str
    version: int
    section: str
    heading: str
    text: str
    score: float


class PolicyRetriever(Protocol):
    def search(
        self, domain: str, at: datetime, query: str, k: int = 5
    ) -> list[RetrievedPassage]: ...


def in_force_filter(domain: str, at: datetime) -> list[dict[str, Any]]:
    if at.tzinfo is None:
        raise ValueError("incident time must be timezone-aware")
    instant = at.astimezone(UTC).isoformat()
    return [
        {"term": {"domain": domain}},
        {"range": {"effective_from": {"lte": instant}}},
        {
            "bool": {
                "should": [
                    {"range": {"effective_until": {"gt": instant}}},
                    {"bool": {"must_not": {"exists": {"field": "effective_until"}}}},
                ],
                "minimum_should_match": 1,
            }
        },
    ]


class ElasticsearchPolicyRetriever:
    """BM25 over heading and text, restricted to the versions in force.

    ``version_filter=False`` exists only so the retrieval benchmark can measure what the filter
    prevents; application code always uses the default.
    """

    def __init__(self, client: Elasticsearch, alias: str, *, version_filter: bool = True) -> None:
        self._client = client
        self._alias = alias
        self._version_filter = version_filter

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        response = self._client.search(
            index=self._alias,
            size=k,
            query={
                "bool": {
                    "filter": (
                        in_force_filter(domain, at)
                        if self._version_filter
                        else [{"term": {"domain": domain}}]
                    ),
                    "must": {"multi_match": {"query": query, "fields": ["heading^2", "text"]}},
                }
            },
        )
        return [_passage(hit) for hit in response["hits"]["hits"]]


def _passage(hit: dict[str, Any]) -> RetrievedPassage:
    source = hit["_source"]
    return RetrievedPassage(
        passage_id=source["passage_id"],
        policy_id=source["policy_id"],
        version=source["version"],
        section=source["section"],
        heading=source["heading"],
        text=source["text"],
        score=float(hit["_score"] or 0.0),
    )


def _documents(session: Session) -> Iterator[dict[str, Any]]:
    rows = session.execute(
        select(PolicyPassage, PolicyVersion, Policy.domain)
        .join(
            PolicyVersion,
            (PolicyVersion.policy_id == PolicyPassage.policy_id)
            & (PolicyVersion.version == PolicyPassage.version),
        )
        .join(Policy, Policy.id == PolicyPassage.policy_id)
        .order_by(PolicyPassage.passage_id)
    )
    for passage, version, domain in rows:
        yield {
            "passage_id": passage.passage_id,
            "policy_id": passage.policy_id,
            "domain": domain,
            "version": passage.version,
            "section": passage.section,
            "heading": passage.heading,
            "text": passage.text,
            "effective_from": version.effective_from.isoformat(),
            "effective_until": (
                version.effective_until.isoformat() if version.effective_until else None
            ),
        }


def rebuild_index(
    client: Elasticsearch, sessions: sessionmaker[Session], alias: str, now: datetime
) -> int:
    """Build a fresh index from PostgreSQL and atomically point the alias at it.

    Searches keep hitting the old index until the swap, and an index that failed to build is
    never exposed. Older indices behind the alias are deleted after the swap.
    """
    name = f"{alias}-{now.strftime('%Y%m%d%H%M%S%f')}"
    client.indices.create(index=name, mappings=MAPPING, settings={"number_of_replicas": 0})
    with sessions() as session:
        actions = (
            {"_index": name, "_id": doc["passage_id"], "_source": doc}
            for doc in _documents(session)
        )
        indexed, _ = helpers.bulk(client, actions, raise_on_error=True)
    client.indices.refresh(index=name)

    previous = (
        list(client.indices.get_alias(name=alias).keys())
        if client.indices.exists_alias(name=alias)
        else []
    )
    client.indices.update_aliases(
        actions=[{"remove": {"index": old, "alias": alias}} for old in previous]
        + [{"add": {"index": name, "alias": alias}}]
    )
    for old in previous:
        client.indices.delete(index=old)
    return int(indexed)
