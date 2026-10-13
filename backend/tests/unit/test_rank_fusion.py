from onramp.policy.search import RetrievedPassage, reciprocal_rank_fusion


def _p(passage_id: str) -> RetrievedPassage:
    return RetrievedPassage(passage_id, "payout", 12, "1.1", "h", "t", 0.0)


def test_passages_found_by_both_lists_rise() -> None:
    lexical = [_p("a"), _p("b"), _p("c")]
    dense = [_p("c"), _p("d"), _p("a")]

    fused = [p.passage_id for p in reciprocal_rank_fusion([lexical, dense])]

    assert fused[:2] == ["a", "c"]
    assert set(fused) == {"a", "b", "c", "d"}


def test_scores_follow_the_formula() -> None:
    fused = reciprocal_rank_fusion([[_p("a")], [_p("b"), _p("a")]], k=60)

    by_id = {p.passage_id: p.score for p in fused}
    assert by_id["a"] == 1 / 61 + 1 / 62
    assert by_id["b"] == 1 / 61


def test_ties_break_deterministically() -> None:
    fused = reciprocal_rank_fusion([[_p("b")], [_p("a")]])

    assert [p.passage_id for p in fused] == ["a", "b"]
