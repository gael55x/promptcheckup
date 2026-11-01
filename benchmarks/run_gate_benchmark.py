"""Benchmark the gate: false-positive rate, detection power, and naive vs paired comparison.

Writes CSV files to benchmarks/results/. Standard library only, seeded, deterministic.
Run from the repository root (takes about a minute):

    PYTHONPATH=src python benchmarks/run_gate_benchmark.py
"""

import csv
import math
import random
from pathlib import Path

from promptcheckup.stats import decide

RESULTS = Path(__file__).parent / "results"
CASE_COUNTS = [20, 30, 50, 80]  # 20 is the gate's default minimum
NOISES = ["normal", "heavy_tailed", "judge", "crash_skew"]
SHIPPED, PLAIN_T, BOOTSTRAP = (
    "shipped: paired t + skew guard",
    "paired t without skew guard",
    "percentile bootstrap (rejected)",
)


def null_samples(kind: str, n: int, rng: random.Random) -> list[float]:
    """Paired differences when the prompt change does nothing (true mean exactly 0)."""
    if kind == "normal":
        return [rng.gauss(0, 1) for _ in range(n)]
    if kind == "heavy_tailed":  # Student t with 3 degrees of freedom
        return [rng.gauss(0, 1) / math.sqrt(rng.gammavariate(1.5, 2) / 3) for _ in range(n)]
    if kind == "judge":  # mean of 2 order-swapped judgments, each -1 / 0 / +1
        return [sum(rng.choice((-1, 0, 0, 1)) for _ in range(2)) / 2 for _ in range(n)]
    if kind == "crash_skew":  # P&L-style: small gains most days, rare large losses
        return [-(math.exp(rng.gauss(0, 1)) - math.exp(0.5)) for _ in range(n)]
    raise ValueError(kind)


def bootstrap_says_better(diffs: list[float], rng: random.Random, resamples: int) -> bool:
    """The percentile bootstrap from the original design, kept only as a reference."""
    n = len(diffs)
    means = sorted(sum(rng.choices(diffs, k=n)) / n for _ in range(resamples))
    return means[math.floor(0.025 * (resamples - 1))] > 0


def says_better(method: str, kind: str, diffs: list[float], rng: random.Random) -> bool:
    if method == BOOTSTRAP:
        return bootstrap_says_better(diffs, rng, resamples=1_000)
    # Judge preferences are bounded in [-1, 1], so the product runs them without the guard.
    guard = method == SHIPPED and kind != "judge"
    return decide(diffs, skew_guard=guard).decision == "better"


def false_positive_rows() -> list[dict]:
    rows = []
    for kind in NOISES:
        for n in CASE_COUNTS:
            for method, sims in ((SHIPPED, 4_000), (PLAIN_T, 4_000), (BOOTSTRAP, 1_000)):
                rng = random.Random(f"fp-{method}-{kind}-{n}")
                hits = sum(
                    says_better(method, kind, null_samples(kind, n, rng), rng) for _ in range(sims)
                )
                rows.append(
                    {
                        "noise": kind,
                        "cases": n,
                        "method": method,
                        "false_better_rate": hits / sims,
                        "simulations": sims,
                    }
                )
    return rows


def power_rows() -> list[dict]:
    """How often the shipped gate detects a real improvement of a given size (in noise SDs)."""
    rows = []
    for effect in (0.2, 0.5, 0.8):
        for n in CASE_COUNTS + [120]:
            rng = random.Random(f"power-{effect}-{n}")
            sims = 2_000
            hits = sum(
                decide([rng.gauss(effect, 1) for _ in range(n)]).decision == "better"
                for _ in range(sims)
            )
            rows.append(
                {"effect_sd": effect, "cases": n, "detected_rate": hits / sims, "simulations": sims}
            )
    return rows


def naive_vs_paired_rows() -> list[dict]:
    """Trading-like scenario: each day's market move is shared noise; B has a real edge.

    Naive: A runs one week, B runs another week, compare mean returns.
    Paired: both prompts replayed on the same days, then the gate decides.
    Each run draws its own weekly market drift, so neither week is rigged.
    """
    days, edge, skill_noise, sims = 20, 0.3, 0.2, 2_000
    rng = random.Random("naive-vs-paired")
    naive = {"correct": 0, "wrong": 0, "no_call": 0}
    paired = {"correct": 0, "wrong": 0, "no_call": 0}
    for _ in range(sims):
        drift_a, drift_b = rng.gauss(0, 1), rng.gauss(0, 1)
        week_a = [rng.gauss(drift_a, 1) for _ in range(days)]
        week_b = [rng.gauss(drift_b, 1) for _ in range(days)]

        mean_a = sum(m + rng.gauss(0, skill_noise) for m in week_a) / days
        mean_b = sum(m + edge + rng.gauss(0, skill_noise) for m in week_b) / days
        naive["correct" if mean_b > mean_a else "wrong"] += 1

        diffs = [
            (m + edge + rng.gauss(0, skill_noise)) - (m + rng.gauss(0, skill_noise)) for m in week_b
        ]
        decision = decide(diffs).decision
        paired[{"better": "correct", "worse": "wrong"}.get(decision, "no_call")] += 1
    return [
        {"method": name, **{k: v / sims for k, v in counts.items()}, "simulations": sims}
        for name, counts in (("naive (different weeks)", naive), ("paired replay", paired))
    ]


def write_csv(name: str, rows: list[dict]) -> None:
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {RESULTS / name}")


if __name__ == "__main__":
    write_csv("false_positive.csv", false_positive_rows())
    write_csv("power.csv", power_rows())
    write_csv("naive_vs_paired.csv", naive_vs_paired_rows())
