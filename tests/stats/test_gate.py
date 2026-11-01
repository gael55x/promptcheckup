import math
import random
from math import comb
from pathlib import Path
from runpy import run_path

import pytest

from promptcheckup.stats import decide, t_quantile

null_samples = run_path(str(Path(__file__).parents[2] / "benchmarks" / "run_gate_benchmark.py"))[
    "null_samples"
]


def noisy(mean: float, n: int, sd: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


def test_clear_positive_shift_is_better():
    verdict = decide(noisy(0.5, 20, 0.2, seed=1))

    assert verdict.decision == "better"
    assert verdict.ci_low > 0


def test_clear_negative_shift_is_worse():
    verdict = decide(noisy(-0.5, 20, 0.2, seed=2))

    assert verdict.decision == "worse"
    assert verdict.ci_high < 0


def test_symmetric_noise_is_inconclusive():
    verdict = decide([-1.0, 1.0] * 10)

    assert verdict.decision == "inconclusive"
    assert verdict.ci_low < 0 < verdict.ci_high


def test_all_ties_is_inconclusive():
    verdict = decide([0.0] * 20)

    assert verdict.decision == "inconclusive"
    assert verdict.ties == 20


def test_too_few_pairs_is_inconclusive_even_when_every_case_improves():
    verdict = decide([1.0] * 19)

    assert verdict.decision == "inconclusive"
    assert "20" in verdict.reason
    assert verdict.ci_low is None and verdict.ci_high is None


def test_counts_wins_losses_and_ties():
    diffs = [0.5, 0.2, -0.1, 0.0, 0.0, 0.3, 0.4, 0.1, 0.6, 0.2, 0.3, 0.1]
    verdict = decide(diffs, min_pairs=10)

    assert (verdict.wins, verdict.losses, verdict.ties) == (9, 1, 2)
    assert verdict.n == 12
    assert verdict.mean == pytest.approx(sum(diffs) / 12)


def test_identical_improvement_on_every_case_is_better():
    verdict = decide([1.0] * 20)

    assert verdict.decision == "better"
    assert verdict.ci_low == verdict.ci_high == 1.0


# Hand computation for 1..10: mean 5.5, sd 3.0276504, standard error 0.9574271.
# t(0.975, 9) = 2.2621572 and t(0.995, 9) = 3.2498355.
@pytest.mark.parametrize(
    ("confidence", "low", "high"),
    [(0.95, 3.3341494, 7.6658506), (0.99, 2.3885194, 8.6114806)],
)
def test_interval_endpoints_match_hand_computation(confidence, low, high):
    verdict = decide([float(i) for i in range(1, 11)], min_pairs=10, confidence=confidence)

    assert verdict.ci_low == pytest.approx(low, abs=1e-6)
    assert verdict.ci_high == pytest.approx(high, abs=1e-6)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_difference_raises(bad):
    with pytest.raises(ValueError):
        decide([0.1] * 20 + [bad])


def test_min_pairs_below_five_is_rejected():
    with pytest.raises(ValueError):
        decide([0.1] * 20, min_pairs=2)


@pytest.mark.parametrize("confidence", [0.4, 0.9999, 0.9999999999999999, 1.0])
def test_unsupported_confidence_is_rejected(confidence):
    with pytest.raises(ValueError):
        decide([0.1] * 20, confidence=confidence)


@pytest.mark.parametrize("scale", [1e-200, 1e200, 1e300])
def test_verdict_does_not_depend_on_the_scale_of_the_scores(scale):
    base = noisy(0.3, 25, 1.0, seed=5)
    expected = decide(base)

    verdict = decide([x * scale for x in base])

    assert verdict.decision == expected.decision
    assert verdict.ci_low == pytest.approx(expected.ci_low * scale, rel=1e-9)


def test_negative_skew_blocks_better():
    # Small gains most days and two large losses: the mean can look good while a rare
    # large loss decides the real outcome.
    diffs = [0.6] * 18 + [-1.0] * 2

    assert decide(diffs, skew_guard=False).decision == "better"
    verdict = decide(diffs)
    assert verdict.decision == "inconclusive"
    assert "skew" in verdict.reason


def test_small_losses_do_not_trigger_the_skew_guard():
    # 19 of 20 cases improved and the one loss is tiny. The shape is negatively skewed,
    # but no single loss is anywhere near the average gain, so nothing is hiding.
    old = [0.62, 0.55, 0.71, 0.40, 0.58, 0.66, 0.49, 0.53, 0.60, 0.44]
    old += [0.57, 0.68, 0.51, 0.47, 0.63, 0.59, 0.42, 0.65, 0.56, 0.50]
    new = [0.70, 0.61, 0.74, 0.52, 0.57, 0.75, 0.58, 0.60, 0.69, 0.47]
    new += [0.66, 0.71, 0.60, 0.55, 0.64, 0.68, 0.50, 0.73, 0.62, 0.58]

    verdict = decide([b - a for a, b in zip(old, new, strict=True)])

    assert verdict.decision == "better"


def test_positive_skew_blocks_worse():
    diffs = [-0.6] * 18 + [1.0] * 2

    assert decide(diffs, skew_guard=False).decision == "worse"
    assert decide(diffs).decision == "inconclusive"


# Standard Student t table values.
@pytest.mark.parametrize(
    ("p", "df", "expected"),
    [
        (0.95, 4, 2.1318),
        (0.975, 4, 2.7764),
        (0.995, 4, 4.6041),
        (0.9995, 4, 8.6103),
        (0.95, 9, 1.8331),
        (0.975, 9, 2.2622),
        (0.995, 9, 3.2498),
        (0.9995, 9, 4.7809),
        (0.95, 29, 1.6991),
        (0.995, 29, 2.7564),
        (0.9995, 29, 3.6594),
        (0.975, 120, 1.9799),
        (0.995, 120, 2.6174),
    ],
)
def test_t_quantile_matches_table(p, df, expected):
    assert t_quantile(p, df) == pytest.approx(expected, abs=1e-4)


# A false "better" is the worst bug this product can have (DESIGN.md, section 3).
# Two-sided 95% means about 2.5% false "better". Limits include Monte Carlo slack; skewed
# noise gets a looser, documented limit because no interval is exact there at small n.
@pytest.mark.parametrize(
    ("kind", "skew_guard", "limit"),
    [
        ("normal", True, 0.03),
        ("heavy_tailed", True, 0.03),
        ("judge", False, 0.03),
        ("crash_skew", True, 0.035),
        ("mild_skew", True, 0.035),
        ("two_point_skew", True, 0.035),
    ],
)
@pytest.mark.parametrize("n", [20, 30])
def test_false_better_rate_is_controlled_when_nothing_changed(kind, skew_guard, limit, n):
    rng = random.Random(f"{kind}-{n}")
    sims = 4_000
    false_better = sum(
        decide(null_samples(kind, n, rng), skew_guard=skew_guard).decision == "better"
        for _ in range(sims)
    )

    assert false_better / sims <= limit


@pytest.mark.parametrize("n", [20, 25, 30, 40, 50])
def test_decisive_judge_false_better_rate_exact(n):
    # Every case a clean win (+1) or loss (-1) with equal chance: enumerate all outcomes.
    rate = (
        sum(
            comb(n, k)
            for k in range(n + 1)
            if decide([1.0] * k + [-1.0] * (n - k), skew_guard=False).decision == "better"
        )
        / 2**n
    )

    assert rate <= 0.035
