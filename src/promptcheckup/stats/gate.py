"""Decide whether prompt B beat prompt A from paired per-case differences."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

Decision = Literal["better", "worse", "inconclusive"]

MIN_CONFIDENCE, MAX_CONFIDENCE = 0.5, 0.999
# Sample skewness beyond this blocks a verdict in the direction skew makes unreliable.
SKEW_LIMIT = 0.5


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


def _t_central_probability(t: float, df: int) -> float:
    """P(|T| <= t) for Student t with integer df (Abramowitz & Stegun 26.7.3, exact)."""
    theta = math.atan(t / math.sqrt(df))
    cos2 = math.cos(theta) ** 2
    term = total = 1.0
    if df % 2 == 0:
        for k in range(1, df // 2):
            term *= cos2 * (2 * k - 1) / (2 * k)
            total += term
        return math.sin(theta) * total
    if df == 1:
        return 2 * theta / math.pi
    for k in range(1, (df - 1) // 2):
        term *= cos2 * (2 * k) / (2 * k + 1)
        total += term
    return 2 / math.pi * (theta + math.sin(theta) * math.cos(theta) * total)


def t_quantile(p: float, df: int) -> float:
    """Upper Student t quantile for 0.5 < p < 1 and integer df >= 1, by bisection."""
    if not 0.5 < p < 1 or df < 1:
        raise ValueError("t_quantile needs 0.5 < p < 1 and df >= 1")
    target = 2 * p - 1
    low, high = 0.0, 1.0
    while _t_central_probability(high, df) < target:
        high *= 2
    for _ in range(100):
        mid = (low + high) / 2
        if _t_central_probability(mid, df) < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def decide(
    diffs: Sequence[float],
    *,
    min_pairs: int = 20,
    confidence: float = 0.95,
    skew_guard: bool = True,
) -> Verdict:
    """Return a conservative verdict for paired differences (B minus A, one per case).

    Uses a paired t confidence interval for the mean difference: anything short of an
    interval that clearly excludes zero is "inconclusive". With `skew_guard`, a verdict
    is also refused when the differences are skewed toward it, for example small gains
    with a few large losses, because no interval is reliable there at small sample sizes.
    Turn it off only for bounded scores such as LLM-judge preferences in [-1, 1].
    """
    values = [float(d) for d in diffs]
    if any(not math.isfinite(v) for v in values):
        raise ValueError("paired differences must be finite numbers")
    if not MIN_CONFIDENCE <= confidence <= MAX_CONFIDENCE:
        raise ValueError(f"confidence must be between {MIN_CONFIDENCE} and {MAX_CONFIDENCE}")
    if min_pairs < 5:
        raise ValueError("min_pairs must be at least 5")

    n = len(values)
    # Work on values scaled into [-1, 1] so tiny or huge scores neither underflow nor overflow.
    scale = max((abs(v) for v in values), default=0.0) or 1.0
    scaled = [v / scale for v in values]
    mean_scaled = sum(scaled) / n if n else 0.0
    wins = sum(v > 0 for v in values)
    losses = sum(v < 0 for v in values)
    counts = {
        "n": n,
        "mean": mean_scaled * scale,
        "wins": wins,
        "losses": losses,
        "ties": n - wins - losses,
    }

    if n < min_pairs:
        return Verdict(
            "inconclusive",
            f"need at least {min_pairs} cases, got {n}",
            ci_low=None,
            ci_high=None,
            **counts,
        )

    deviations = [v - mean_scaled for v in scaled]
    sd = math.sqrt(sum(d * d for d in deviations) / (n - 1))
    half_width = t_quantile(1 - (1 - confidence) / 2, n - 1) * sd / math.sqrt(n)
    low, high = mean_scaled - half_width, mean_scaled + half_width
    skewness = (sum(d**3 for d in deviations) / n) / sd**3 if sd > 0 else 0.0
    interval = {"ci_low": low * scale, "ci_high": high * scale}

    if low > 0:
        if skew_guard and skewness < -SKEW_LIMIT:
            return Verdict(
                "inconclusive",
                f"looks better, but the differences are negatively skewed ({skewness:.2f}): "
                "a few large losses could decide the outcome; add more cases",
                **interval,
                **counts,
            )
        return Verdict(
            "better", "the whole confidence interval is above zero", **interval, **counts
        )
    if high < 0:
        if skew_guard and skewness > SKEW_LIMIT:
            return Verdict(
                "inconclusive",
                f"looks worse, but the differences are positively skewed ({skewness:.2f}): "
                "a few large gains could decide the outcome; add more cases",
                **interval,
                **counts,
            )
        return Verdict("worse", "the whole confidence interval is below zero", **interval, **counts)
    return Verdict("inconclusive", "the confidence interval includes zero", **interval, **counts)
