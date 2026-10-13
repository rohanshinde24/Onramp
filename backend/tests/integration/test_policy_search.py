import os
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from elasticsearch import Elasticsearch
from sqlalchemy.orm import Session, sessionmaker

from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents
from onramp.policy.search import (
    DensePolicyRetriever,
    ElasticsearchPolicyRetriever,
    HybridPolicyRetriever,
    rebuild_index,
)

pytestmark = pytest.mark.search

CORPUS = Path(__file__).resolve().parents[3] / "policies"
ES_URL = os.environ.get("ELASTICSEARCH_URL", "http://127.0.0.1:9200")
JUNE = datetime(2026, 6, 28, tzinfo=UTC)  # payout v11, quality v5, onboarding v3
JULY = datetime(2026, 7, 3, tzinfo=UTC)  # payout v12
SEPT = datetime(2026, 9, 10, tzinfo=UTC)  # quality v6, onboarding v4


@pytest.fixture
def client() -> Elasticsearch:
    return Elasticsearch(ES_URL)


@pytest.fixture
def alias(client: Elasticsearch) -> Iterator[str]:
    name = f"test-policy-{uuid.uuid4().hex[:8]}"
    yield name
    # Elasticsearch refuses wildcard deletes by default, so delete by resolved name.
    if client.indices.exists_alias(name=name):
        for index in client.indices.get_alias(name=name):
            client.indices.delete(index=index)


@pytest.fixture
def retriever(
    client: Elasticsearch, alias: str, onramp_sessions: sessionmaker[Session]
) -> ElasticsearchPolicyRetriever:
    with onramp_sessions() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    rebuild_index(client, onramp_sessions, alias, datetime.now(UTC))
    return ElasticsearchPolicyRetriever(client, alias)


@pytest.mark.parametrize(
    ("at", "expected"),
    [(JUNE, "payout_v11_section_4_2"), (JULY, "payout_v12_section_4_2")],
)
def test_returns_the_passage_from_the_version_in_force(
    retriever: ElasticsearchPolicyRetriever, at: datetime, expected: str
) -> None:
    # Ranking quality is measured by the retrieval benchmark; this checks version selection.
    results = retriever.search("payout", at, "how long until an authorized payment settles", k=3)

    assert expected in [r.passage_id for r in results]


def test_never_returns_another_version_or_domain(retriever: ElasticsearchPolicyRetriever) -> None:
    results = retriever.search("payout", JULY, "payment settlement window missing", k=20)

    assert results
    assert {(r.policy_id, r.version) for r in results} == {("payout", 12)}


def test_tax_form_requirement_only_exists_from_version_4(
    retriever: ElasticsearchPolicyRetriever,
) -> None:
    before = retriever.search("onboarding", JUNE, "tax form required before activation", k=1)
    after = retriever.search("onboarding", SEPT, "tax form required before activation", k=1)

    assert before[0].passage_id == "onboarding_v3_section_2_2"
    assert "not required" in before[0].text
    assert after[0].passage_id == "onboarding_v4_section_2_2"


def test_rebuild_swaps_alias_to_a_single_new_index(
    client: Elasticsearch,
    alias: str,
    onramp_sessions: sessionmaker[Session],
    retriever: ElasticsearchPolicyRetriever,
) -> None:
    first = set(client.indices.get_alias(name=alias))

    count = rebuild_index(client, onramp_sessions, alias, datetime.now(UTC))

    second = set(client.indices.get_alias(name=alias))
    assert count == sum(len(d.passages) for d in load_corpus(CORPUS))
    assert len(second) == 1
    assert second.isdisjoint(first)
    assert not client.indices.exists(index=next(iter(first)))


def test_naive_incident_time_is_rejected(retriever: ElasticsearchPolicyRetriever) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        retriever.search("payout", datetime(2026, 7, 3), "settlement")


class HashingEmbedder:
    """Deterministic bag-of-words vectors: enough to exercise kNN and filtering without a model."""

    dimensions = 64

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in text.lower().split():
            vector[hash_word(word) % self.dimensions] += 1.0
        vector[0] += 0.001  # never all zeros, which cosine similarity rejects
        return vector


def hash_word(word: str) -> int:
    return sum(ord(c) * (i + 1) for i, c in enumerate(word.strip(".,?")))


def test_dense_retrieval_respects_the_version_filter(
    client: Elasticsearch, alias: str, onramp_sessions: sessionmaker[Session]
) -> None:
    with onramp_sessions() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    embedder = HashingEmbedder()
    rebuild_index(client, onramp_sessions, alias, datetime.now(UTC), embedder)
    dense = DensePolicyRetriever(client, alias, embedder)

    results = dense.search("payout", JULY, "settlement window calendar days", k=10)

    assert results
    assert {(r.policy_id, r.version) for r in results} == {("payout", 12)}


def test_hybrid_combines_both_retrievers(
    client: Elasticsearch, alias: str, onramp_sessions: sessionmaker[Session]
) -> None:
    with onramp_sessions() as session, session.begin():
        load_documents(session, load_corpus(CORPUS))
    embedder = HashingEmbedder()
    rebuild_index(client, onramp_sessions, alias, datetime.now(UTC), embedder)
    hybrid = HybridPolicyRetriever(
        ElasticsearchPolicyRetriever(client, alias), DensePolicyRetriever(client, alias, embedder)
    )

    results = hybrid.search("payout", JULY, "settlement window", k=5)

    assert len(results) == 5
    assert {(r.policy_id, r.version) for r in results} == {("payout", 12)}
