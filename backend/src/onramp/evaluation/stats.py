"""Uncertainty for small benchmark comparisons."""

import random
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class PairedDifference:
    mean: float
    low: float
    high: float

    @property
    def excludes_zero(self) -> bool:
        return self.low > 0 or self.high < 0


def paired_bootstrap(
    a: Mapping[str, float],
    b: Mapping[str, float],
    *,
    resamples: int = 10_000,
    seed: int = 1,
    confidence: float = 0.95,
) -> PairedDifference:
    """Mean of a - b over shared items, with a percentile bootstrap confidence interval.

    Items are resampled in pairs, so per-item difficulty cancels out.
    """
    keys = sorted(a.keys() & b.keys())
    if not keys:
        raise ValueError("no shared items")
    diffs = [a[k] - b[k] for k in keys]
    rng = random.Random(seed)  # noqa: S311 - statistics, not security
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    tail = (1 - confidence) / 2
    return PairedDifference(
        mean=sum(diffs) / n,
        low=means[int(tail * resamples)],
        high=means[min(int((1 - tail) * resamples), resamples - 1)],
    )
