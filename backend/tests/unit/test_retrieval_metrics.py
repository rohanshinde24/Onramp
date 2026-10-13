import pytest

from onramp.evaluation.retrieval import recall_at, reciprocal_rank, stale_fraction


def test_recall_counts_relevant_passages_in_the_top_k() -> None:
    retrieved = ["a", "b", "c", "d"]

    assert recall_at(retrieved, ["c"], 1) == 0.0
    assert recall_at(retrieved, ["c"], 3) == 1.0
    assert recall_at(retrieved, ["a", "z"], 5) == 0.5


def test_reciprocal_rank_uses_the_first_relevant_hit() -> None:
    assert reciprocal_rank(["a", "b", "c"], ["b", "c"]) == 0.5
    assert reciprocal_rank(["a", "b"], ["z"]) == 0.0
    assert reciprocal_rank([], ["z"]) == 0.0


def test_stale_fraction() -> None:
    in_force = {("payout", 12)}

    assert stale_fraction([("payout", 12), ("payout", 11)], in_force) == 0.5
    assert stale_fraction([], in_force) == 0.0


def test_duplicate_retrieved_ids_do_not_inflate_recall() -> None:
    assert recall_at(["a", "a", "a"], ["a", "b"], 3) == pytest.approx(0.5)
