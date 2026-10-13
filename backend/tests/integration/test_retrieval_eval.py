"""The retrieval evaluator itself: it must reward a correct retriever, catch a stale one, and
refuse wrong labels. These use fake retrievers, so they need no Elasticsearch."""

from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from onramp.evaluation.retrieval import (
    LabeledQuery,
    LabelError,
    QuerySet,
    evaluate,
    load_queries,
    summarize,
    validate_labels,
)
from onramp.policy.corpus import load_corpus
from onramp.policy.loader import load_documents
from onramp.policy.models import PolicyPassage
from onramp.policy.search import RetrievedPassage

ROOT = Path(__file__).resolve().parents[3]
QUERIES = ROOT / "eval" / "retrieval" / "queries.yaml"


def _hit(session: Session, passage_id: str) -> RetrievedPassage:
    p = session.get_one(PolicyPassage, passage_id)
    return RetrievedPassage(p.passage_id, p.policy_id, p.version, p.section, p.heading, p.text, 1.0)


class OracleRetriever:
    def __init__(self, session: Session, query_set: QuerySet) -> None:
        self._session = session
        self._answers = {(q.domain, q.at, q.query): q.relevant for q in query_set.queries}

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        return [_hit(self._session, pid) for pid in self._answers[(domain, at, query)]][:k]


class StaleRetriever(OracleRetriever):
    """Right section, previous version: what an unfiltered search tends to return."""

    def search(self, domain: str, at: datetime, query: str, k: int = 5) -> list[RetrievedPassage]:
        hits = []
        for hit in super().search(domain, at, query, k):
            older = hit.passage_id.replace(f"_v{hit.version}_", f"_v{hit.version - 1}_")
            if self._session.get(PolicyPassage, older) is not None:
                hits.append(_hit(self._session, older))
        return hits


@pytest.fixture
def session(onramp_session: Session) -> Session:
    load_documents(onramp_session, load_corpus(ROOT / "policies"))
    return onramp_session


def test_real_labels_are_valid(session: Session) -> None:
    validate_labels(session, load_queries(QUERIES))


def test_oracle_scores_perfectly(session: Session) -> None:
    query_set = load_queries(QUERIES)

    summary = summarize(evaluate(session, query_set, "oracle", OracleRetriever(session, query_set)))

    assert summary["recall@5"] == 1.0
    assert summary["mrr@10"] == 1.0
    assert summary["stale_fraction@10"] == 0.0


def test_stale_retriever_is_caught(session: Session) -> None:
    query_set = load_queries(QUERIES)

    summary = summarize(evaluate(session, query_set, "stale", StaleRetriever(session, query_set)))

    assert summary["recall@5"] < 0.5
    assert summary["top1_stale_rate"] > 0.3


def test_label_from_a_version_not_in_force_is_rejected(session: Session) -> None:
    bad = QuerySet(
        version=1,
        queries=[
            LabeledQuery(
                id="x",
                domain="payout",
                at=datetime.fromisoformat("2026-08-21T12:00:00+00:00"),
                query="settlement window",
                relevant=("payout_v11_section_4_2",),
            )
        ],
    )

    with pytest.raises(LabelError, match="not in force"):
        validate_labels(session, bad)


def test_unknown_passage_label_is_rejected(session: Session) -> None:
    bad = QuerySet(
        version=1,
        queries=[
            LabeledQuery(
                id="x",
                domain="payout",
                at=datetime.fromisoformat("2026-08-21T12:00:00+00:00"),
                query="settlement window",
                relevant=("payout_v12_section_9_9",),
            )
        ],
    )

    with pytest.raises(LabelError, match="unknown passage"):
        validate_labels(session, bad)
