import pytest

from onramp.evaluation.stats import paired_bootstrap


def test_identical_methods_have_zero_difference() -> None:
    scores = {f"q{i}": i / 10 for i in range(10)}

    result = paired_bootstrap(scores, scores)

    assert (result.mean, result.low, result.high) == (0.0, 0.0, 0.0)
    assert not result.excludes_zero


def test_consistent_improvement_excludes_zero() -> None:
    a = {f"q{i}": 1.0 for i in range(30)}
    b = {f"q{i}": 0.5 for i in range(30)}

    result = paired_bootstrap(a, b)

    assert result.mean == pytest.approx(0.5)
    assert result.excludes_zero


def test_mixed_results_straddle_zero() -> None:
    a = {f"q{i}": 1.0 if i % 2 else 0.0 for i in range(20)}
    b = {f"q{i}": 0.0 if i % 2 else 1.0 for i in range(20)}

    assert not paired_bootstrap(a, b).excludes_zero


def test_is_deterministic_for_a_seed() -> None:
    a = {f"q{i}": (i * 7 % 10) / 10 for i in range(25)}
    b = {f"q{i}": (i * 3 % 10) / 10 for i in range(25)}

    assert paired_bootstrap(a, b, seed=3) == paired_bootstrap(a, b, seed=3)


def test_requires_shared_items() -> None:
    with pytest.raises(ValueError, match="no shared items"):
        paired_bootstrap({"a": 1.0}, {"b": 1.0})
