"""Decide whether prompt B beat prompt A from paired per-case differences."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist
from typing import Literal

Decision = Literal["better", "worse", "inconclusive"]


@dataclass(frozen=True)
class Verdict:
    decision: Decision
    reason: str
    n: int
    mean: float
    ci_low: float | None
    ci_high: float | None
    wins: int
    losses: int
    ties: int


def t_quantile(p: float, df: int) -> float:
    """Student t quantile via the Cornish-Fisher expansion (within 0.001 of tables for df >= 4)."""
    z = NormalDist().inv_cdf(p)
    g1 = (z**3 + z) / 4
    g2 = (5 * z**5 + 16 * z**3 + 3 * z) / 96
    g3 = (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / 384
    g4 = (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / 92160
    return z + g1 / df + g2 / df**2 + g3 / df**3 + g4 / df**4


def decide(diffs: Sequence[float], *, min_pairs: int = 10, confidence: float = 0.95) -> Verdict:
    """Return a conservative verdict for paired differences (B minus A, one per case).

    Uses a paired t confidence interval for the mean difference. A percentile bootstrap
    was measured first and called "better" too often when nothing had changed at small n.
    Anything short of an interval that clearly excludes zero is "inconclusive".
    """
    values = [float(d) for d in diffs]
    if any(not math.isfinite(v) for v in values):
        raise ValueError("paired differences must be finite numbers")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if min_pairs < 5:
        raise ValueError("min_pairs must be at least 5")

    n = len(values)
    mean = sum(values) / n if n else 0.0
    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)
    counts = {"n": n, "mean": mean, "wins": wins, "losses": losses, "ties": n - wins - losses}

    if n < min_pairs:
        return Verdict(
            "inconclusive",
            f"need at least {min_pairs} cases, got {n}",
            ci_low=None,
            ci_high=None,
            **counts,
        )

    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half_width = t_quantile(1 - (1 - confidence) / 2, n - 1) * sd / math.sqrt(n)
    ci_low, ci_high = mean - half_width, mean + half_width

    if ci_low > 0:
        decision, reason = "better", "the whole confidence interval is above zero"
    elif ci_high < 0:
        decision, reason = "worse", "the whole confidence interval is below zero"
    else:
        decision, reason = "inconclusive", "the confidence interval includes zero"
    return Verdict(decision, reason, ci_low=ci_low, ci_high=ci_high, **counts)
