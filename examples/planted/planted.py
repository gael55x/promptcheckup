"""Planted example: a trading-like agent where the market move is shared noise.

Each trading day has a market move that hits every prompt equally. Prompt B carries a
small real edge over prompt A. Comparing A's week with B's week (the naive way) mostly
measures the market. Running both prompts on the same days (paired replay) cancels it.

Run from the repository root:

    PYTHONPATH=src python examples/planted/planted.py
"""

import random
from typing import NamedTuple

DAYS_PER_WEEK = 20
SKILL_NOISE = 0.2  # day-to-day noise in the agent's own decisions


class Weeks(NamedTuple):
    first: list[float]  # rising market: prompt A ran here
    second: list[float]  # falling market: prompt B ran here


def make_weeks(seed: int = 0) -> Weeks:
    rng = random.Random(seed)
    first = [rng.gauss(1.0, 1.0) for _ in range(DAYS_PER_WEEK)]
    second = [rng.gauss(-1.0, 1.0) for _ in range(DAYS_PER_WEEK)]
    return Weeks(first, second)


def daily_return(market: float, edge: float, rng: random.Random) -> float:
    return market + edge + rng.gauss(0.0, SKILL_NOISE)


def naive_difference(weeks: Weeks, edge: float, seed: int = 0) -> float:
    """Mean return of B in its week minus mean return of A in its (different) week."""
    rng = random.Random(seed)
    a = [daily_return(m, 0.0, rng) for m in weeks.first]
    b = [daily_return(m, edge, rng) for m in weeks.second]
    return sum(b) / len(b) - sum(a) / len(a)


def paired_differences(markets: list[float], edge: float, seed: int = 0) -> list[float]:
    """Both prompts replayed on the same days: B minus A, one value per day."""
    rng = random.Random(seed)
    return [daily_return(m, edge, rng) - daily_return(m, 0.0, rng) for m in markets]


if __name__ == "__main__":
    from promptcheckup import decide

    weeks = make_weeks()
    naive = naive_difference(weeks, edge=0.3)
    verdict = decide(paired_differences(weeks.second, edge=0.3))
    print(f"Naive comparison (different weeks): B - A = {naive:+.2f}  -> picks A")
    print(
        f"Paired replay (same days): mean B - A = {verdict.mean:+.2f}, "
        f"95% CI [{verdict.ci_low:+.2f}, {verdict.ci_high:+.2f}] -> {verdict.decision}"
    )
