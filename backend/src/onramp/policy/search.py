"""Policy passage search (ADR-003).

The index is a rebuildable projection of PostgreSQL. Every query filters to the case's domain
and to versions in force at the incident time before any ranking, so a passage from the wrong
version cannot be returned however relevant its text is.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from elasticsearch import Elasticsearch, helpers
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from onramp.policy.embeddings import Embedder
from onramp.policy.models import Policy, PolicyPassage, PolicyVersion


def mapping(embedding_dimensions: int | None = None) -> dict[str, Any]:
    properties = dict(_PROPERTIES)
    if embedding_dimensions is not None:
        properties["embedding"] = {
            "type": "dense_vector",
            "dims": embedding_dimensions,
            "index": True,
            "similarity": "cosine",
        }
    return {"dynamic": "strict", "properties": properties}


_PROPERTIES: dict[str, Any] = {
    "passage_id": {"type": "keyword"},
    "policy_id": {"type": "keyword"},
    "domain": {"type": "keyword"},
    "version": {"type": "integer"},
    "section": {"type": "keyword"},
    "heading": {"type": "text", "analyzer": "english"},
    "text": {"type": "text", "analyzer": "english"},
    "effective_from": {"type": "date"},
    "effective_until": {"type": "date"},
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


class DensePolicyRetriever:
    """kNN over passage embeddings, with the same in-force filter applied inside the search."""

    def __init__(self, client: Elasticsearch, alias: str, embedder: Embedder) -> None:
        self._client = client
        self._alias = alias
        self._embedder = embedder

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        response = self._client.search(
            index=self._alias,
            size=k,
            knn={
                "field": "embedding",
                "query_vector": self._embedder.embed_query(query),
                "k": k,
                "num_candidates": max(50, k * 5),
                "filter": in_force_filter(domain, at),
            },
            source_excludes=["embedding"],
        )
        return [_passage(hit) for hit in response["hits"]["hits"]]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievedPassage]], *, k: int = 60
) -> list[RetrievedPassage]:
    """Fuse ranked lists by summing 1 / (k + rank). Passages found by several lists rise."""
    scores: dict[str, float] = {}
    passages: dict[str, RetrievedPassage] = {}
    for ranking in rankings:
        for rank, passage in enumerate(ranking, start=1):
            scores[passage.passage_id] = scores.get(passage.passage_id, 0.0) + 1.0 / (k + rank)
            passages.setdefault(passage.passage_id, passage)
    ordered = sorted(scores, key=lambda pid: (-scores[pid], pid))
    return [replace(passages[pid], score=scores[pid]) for pid in ordered]


class HybridPolicyRetriever:
    """BM25 and dense results fused with reciprocal rank fusion."""

    def __init__(
        self, lexical: PolicyRetriever, dense: PolicyRetriever, *, depth: int = 20
    ) -> None:
        self._lexical = lexical
        self._dense = dense
        self._depth = depth

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        fused = reciprocal_rank_fusion(
            [
                self._lexical.search(domain, at, query, self._depth),
                self._dense.search(domain, at, query, self._depth),
            ]
        )
        return fused[:k]


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
    client: Elasticsearch,
    sessions: sessionmaker[Session],
    alias: str,
    now: datetime,
    embedder: Embedder | None = None,
) -> int:
    """Build a fresh index from PostgreSQL and atomically point the alias at it.

    Searches keep hitting the old index until the swap, and an index that failed to build is
    never exposed. Older indices behind the alias are deleted after the swap.
    """
    name = f"{alias}-{now.strftime('%Y%m%d%H%M%S%f')}"
    dimensions = embedder.dimensions if embedder else None
    client.indices.create(
        index=name, mappings=mapping(dimensions), settings={"number_of_replicas": 0}
    )
    with sessions() as session:
        documents = list(_documents(session))
    if embedder is not None:
        vectors = embedder.embed_documents([f"{d['heading']}. {d['text']}" for d in documents])
        for document, vector in zip(documents, vectors, strict=True):
            document["embedding"] = vector
    actions = ({"_index": name, "_id": d["passage_id"], "_source": d} for d in documents)
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


def default_retriever(client: Elasticsearch, alias: str, embedder: Embedder) -> PolicyRetriever:
    """The retriever the application uses (ADR-011)."""
    return DensePolicyRetriever(client, alias, embedder)
