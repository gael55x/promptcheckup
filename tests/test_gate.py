import math
import random

import pytest

from promptcheckup.gate import decide, t_quantile


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
    verdict = decide([1.0] * 5, min_pairs=10)

    assert verdict.decision == "inconclusive"
    assert "10" in verdict.reason
    assert verdict.ci_low is None and verdict.ci_high is None


def test_counts_wins_losses_and_ties():
    verdict = decide([0.5, 0.2, -0.1, 0.0, 0.0, 0.3, 0.4, 0.1, 0.6, 0.2, 0.3, 0.1])

    assert (verdict.wins, verdict.losses, verdict.ties) == (9, 1, 2)
    assert verdict.n == 12
    assert verdict.mean == pytest.approx(
        sum([0.5, 0.2, -0.1, 0.3, 0.4, 0.1, 0.6, 0.2, 0.3, 0.1]) / 12
    )


def test_identical_improvement_on_every_case_is_better():
    verdict = decide([1.0] * 12)

    assert verdict.decision == "better"
    assert verdict.ci_low == verdict.ci_high == 1.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_difference_raises(bad):
    with pytest.raises(ValueError):
        decide([0.1] * 12 + [bad])


def test_min_pairs_below_five_is_rejected():
    with pytest.raises(ValueError):
        decide([0.1] * 12, min_pairs=2)


# Standard Student t table, 0.975 quantile.
@pytest.mark.parametrize(
    ("df", "expected"),
    [(4, 2.7764), (9, 2.2622), (19, 2.0930), (29, 2.0452), (120, 1.9799)],
)
def test_t_quantile_matches_table(df, expected):
    assert t_quantile(0.975, df) == pytest.approx(expected, abs=0.001)


def null_samples(kind: str, n: int, rng: random.Random) -> list[float]:
    if kind == "normal":
        return [rng.gauss(0, 1) for _ in range(n)]
    if kind == "heavy_tailed":  # Student t with 3 degrees of freedom
        return [rng.gauss(0, 1) / math.sqrt(rng.gammavariate(1.5, 2) / 3) for _ in range(n)]
    if kind == "judge":  # mean of 2 samples, each -1 / 0 / +1, symmetric
        return [sum(rng.choice((-1, 0, 0, 1)) for _ in range(2)) / 2 for _ in range(n)]
    raise AssertionError(kind)


@pytest.mark.parametrize("kind", ["normal", "heavy_tailed", "judge"])
def test_false_better_rate_is_controlled_when_nothing_changed(kind):
    # A false "better" is the worst bug this product can have (DESIGN.md, section 3).
    # Two-sided 95% means at most about 2.5% false "better"; 3% allows Monte Carlo slack.
    # Checked at the default min_pairs, the smallest sample the gate will judge.
    rng = random.Random(42)
    sims = 4_000
    false_better = sum(
        decide(null_samples(kind, 10, rng)).decision == "better" for _ in range(sims)
    )

    assert false_better / sims <= 0.03
