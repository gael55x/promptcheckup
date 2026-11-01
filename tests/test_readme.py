"""The README's claims, recomputed from saved results and code, and its release hygiene."""

import csv
import random
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from math import comb
from pathlib import Path
from runpy import run_path

import pytest

from promptcheckup.checkup.evaluate import planned_calls
from promptcheckup.checkup.split import split_cases
from promptcheckup.cli.config import RunConfig
from promptcheckup.data import Case
from promptcheckup.stats import decide

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
RESULTS = ROOT / "benchmarks/results"
DRAFT_WORDING = ("not on pypi", "pre-release", "in progress", "early development", "coming soon")


def csv_rows(name):
    with (RESULTS / name).open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def percent(value):
    return f"{(100 * Decimal(str(value))).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def drifted(row):
    return row["drift_decision"] == "worse"


@pytest.mark.parametrize(
    ("incident", "predicate", "label", "quoted"),
    [
        ("harmful_provider", drifted, "Provider change that made answers worse (0.5 SD)", "87.5%"),
        ("harmless_provider", drifted, "Provider change with no effect on quality", "2.0%"),
        (
            "real_fix",
            lambda row: row["proposal_outcome"] == "kept",
            "Real fix proposed (+1 SD)",
            "90.5%",
        ),
        (
            "useless_fix",
            lambda row: row["proposal_outcome"] == "rejected",
            "Useless edit proposed",
            "97.5%",
        ),
    ],
)
def test_simulated_year_numbers_match_the_committed_csv(incident, predicate, label, quoted):
    rows = [row for row in csv_rows("checkup_simulation.csv") if row["incident"] == incident]
    count = sum(map(predicate, rows))
    assert len(rows) == 200
    assert percent(count / len(rows)) == quoted
    table_row = next(line for line in README.splitlines() if line.startswith(f"| {label} |"))
    assert quoted in table_row
    if incident in ("real_fix", "useless_fix"):
        assert f"({count}/{len(rows)})" in table_row


def test_regression_sizes_and_counts_follow_each_repeats_real_fix():
    rows = csv_rows("checkup_simulation.csv")
    kept = {
        row["repeat"]: row["proposal_outcome"] == "kept"
        for row in rows
        if row["incident"] == "real_fix"
    }
    regressions = [row for row in rows if row["incident"] == "unreviewed_regression"]
    assert len(regressions) == len(kept) == 200
    assert sum(map(drifted, regressions)) == 197
    for prior_kept, size, expected in ((True, 2, (181, 181)), (False, 1, (16, 19))):
        stratum = [row for row in regressions if kept[row["repeat"]] == prior_kept]
        flagged = sum(map(drifted, stratum))
        assert (flagged, len(stratum)) == expected
        assert (
            f"({size} SD drop) | flagged in {percent(flagged / len(stratum))} "
            f"({flagged}/{len(stratum)} years) |"
        ) in README


def test_quiet_week_rate_excludes_first_weeks_without_a_baseline():
    quiet = [row for row in csv_rows("checkup_simulation.csv") if row["incident"] == "quiet"]
    first = [row for row in quiet if row["drift_decision"] == "no_baseline"]
    assessable = [row for row in quiet if row["drift_decision"] != "no_baseline"]
    flagged = sum(map(drifted, assessable))
    assert {row["week"] for row in first} == {"1"}
    assert (len(quiet), len(first), len(assessable), flagged) == (9400, 200, 9200, 216)
    assert (
        f"{flagged} false alarms in {len(assessable):,} assessable weeks "
        f"({percent(flagged / len(assessable))})"
    ) in README
    assert (
        f"{len(quiet):,} quiet weeks include {len(first)} first weeks without a baseline" in README
    )


def test_simulation_setup_and_assumptions_are_stated():
    simulation = run_path(str(ROOT / "benchmarks/run_checkup_simulation.py"))
    config = simulation["Config"]()
    rows = csv_rows("checkup_simulation.csv")
    assert len({row["repeat"] for row in rows}) == config.repeats == 200
    assert len({row["week"] for row in rows}) == config.weeks == 52
    assert config.cases == 50 and config.samples == 2 and len(config.incidents) == 5
    held = split_cases(simulation["make_cases"](config), simulation["HOLDOUT_SHARE"])[1]
    assert len(held) == 25
    assert "200 repeats of 52 weeks" in README
    assert "50 fixed cases, 2 samples and five planted incidents" in README
    assert f"proposals were gated on {len(held)} held-out cases" in README
    assert (
        "Gaussian noise, fixed cases, scripted proposals and effects of known size, "
        "with no real model"
    ) in README


def test_useless_edit_null_check_is_pinned_to_its_own_csv():
    rows = csv_rows("useless_fix_null_check.csv")
    counts = {row["decision"]: int(row["count"]) for row in rows}
    trials = {int(row["trials"]) for row in rows}
    assert trials == {2000}
    assert counts == {"better": 48, "worse": 39, "inconclusive": 1913}
    total = sum(counts.values())
    assert {total} == trials
    assert (
        f"another {total:,} times on the 25 held-out cases wrongly kept it "
        f"{counts['better']} times ({percent(counts['better'] / total)})"
    ) in README


def test_naive_versus_paired_numbers_match_the_committed_csv():
    rows = {row["method"]: row for row in csv_rows("naive_vs_paired.csv")}
    naive, paired = rows["naive (different weeks)"], rows["paired replay"]
    assert f"worse prompt in {percent(naive['wrong'])} of runs" in README
    assert paired["simulations"] == naive["simulations"] == "2000"
    assert float(paired["wrong"]) == 0
    assert sum(Decimal(paired[key]) for key in ("correct", "wrong", "no_call")) == 1
    assert (
        f"picked the better one in {percent(paired['correct'])}, never picked the worse one "
        f"in 2,000 runs, and said not sure yet in the other {percent(paired['no_call'])}"
    ) in README


def test_example_numbers_match_their_saved_reports():
    reports = list((ROOT / "examples/support-bot/results").glob("report-*.md"))
    assert len(reports) == 3
    for report in reports:
        text = report.read_text()
        assert "promptcheckup version: 0.0.1." in text
        assert "Self-check: reliable; consistency 100.0%." in text
        assert re.search(r"^\| 30 \|", text, re.M)
    haiku = (ROOT / "examples/support-bot/results/report-claude-haiku-5-5.md").read_text()
    trading = (ROOT / "examples/trading/results/report-claude-haiku-5-5.md").read_text()
    assert haiku.splitlines()[0] in README
    tokens = re.search(r"Tokens: (\d+) input, (\d+) output", haiku).groups()
    assert all(f"{int(count):,}" in README for count in tokens)
    calls = int(re.search(r"(\d+) judge\.", haiku)[1])
    assert f"{calls} judge calls" in README
    mean, low, high, wins, losses, ties = re.search(
        r"\| 60 \| ([+-][\d.]+) \| ([+-][\d.]+) to ([+-][\d.]+) \| (\d+) \| (\d+) \| (\d+) \|",
        trading,
    ).groups()
    assert mean in README and low.lstrip("-") in README and high in README
    assert f"won {wins} cases and lost {losses}" in README
    assert f"on {int(wins) + int(losses) + int(ties)} cases" in README
    assert "promptcheckup version: 0.0.1." in trading
    assert "promptcheckup 0.0.1" in README and "do not validate 1.1.0" in README
    assert "self-check with 100.0% order consistency" in README


def test_trading_full_market_mean_is_scoped_to_the_generated_market(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "examples/trading"))
    for name in ("market", "pnl", "trader"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    policies = run_path(str(ROOT / "examples/trading/check_policies.py"))
    verdict = policies["market_check"]()
    assert verdict.n == 2085 and f"{verdict.mean:+.2f}" == "+1.37"
    assert (
        f"all {verdict.n:,} eligible ticker-days in this generated market gives a mean of "
        f"{verdict.mean:+.2f} bp"
    ) in README


def test_readme_has_no_draft_wording():
    lowered = README.lower()
    assert [phrase for phrase in DRAFT_WORDING if phrase in lowered] == []


def test_readme_images_and_file_links_work_on_pypi():
    images = re.findall(r'<img src="([^"]+)"|!\[[^\]]*\]\(([^)]+)\)', README)
    sources = [first or second for first, second in images]
    assert sources and all(source.startswith("https://") for source in sources)
    links = re.findall(r"\]\(([^)#][^)]*)\)", README)
    assert all(link.startswith(("https://", "mailto:")) for link in links)


def test_power_table_matches_the_committed_csv():
    rows = csv_rows("power.csv")
    rate = {(row["effect_sd"], row["cases"]): row["detected_rate"] for row in rows}
    assert {row["simulations"] for row in rows} == {"2000"}
    for label, effect in (("Large", "0.8"), ("Medium", "0.5"), ("Small", "0.2")):
        cells = " | ".join(percent(rate[effect, cases]) for cases in ("20", "50", "120"))
        assert f"| {label} ({effect} SD) | {cells} |" in README
    assert "normally distributed, independent per-case differences" in README
    assert "2,000 simulations per cell" in README


def rate_range(values):
    return f"{percent(min(values))[:-1]}–{percent(max(values))}"


def test_false_better_ranges_match_the_committed_csv():
    rows = csv_rows("false_positive.csv")
    shipped = [row for row in rows if row["method"].startswith("shipped")]
    symmetric = [float(r["false_better_rate"]) for r in shipped if r["noise"] != "crash_skew"]
    crash = {
        r["cases"]: float(r["false_better_rate"]) for r in shipped if r["noise"] == "crash_skew"
    }
    plain = [
        float(r["false_better_rate"])
        for r in rows
        if r["method"] == "paired t without skew guard" and r["noise"] == "crash_skew"
    ]
    assert rate_range(symmetric) == "2.2–2.7%"
    assert "2.2–2.7%" in README and max(symmetric) < 0.027
    assert percent(crash["20"]) == "2.0%" and crash["50"] < 0.001
    assert "2.0% at 20 cases" in README
    assert max(crash["50"], crash["80"]) < 0.001  # "almost never at 50 or more"
    assert "almost never at 50 or more" in README
    assert rate_range(plain) == "8.9–12.7%" and "8.9–12.7%" in README


def test_additional_worst_cases_are_named_beside_benchmark_results():
    benchmark = run_path(str(ROOT / "benchmarks/run_gate_benchmark.py"))
    rng = random.Random("mild_skew-20")  # same experiment as tests/stats/test_gate.py
    hits = sum(
        decide(benchmark["null_samples"]("mild_skew", 20, rng)).decision == "better"
        for _ in range(4000)
    )
    decisive = (
        sum(
            comb(50, wins)
            for wins in range(51)
            if decide([1.0] * wins + [-1.0] * (50 - wins), skew_guard=False).decision == "better"
        )
        / 2**50
    )
    assert hits == 112 and percent(hits / 4000) == "2.8%"
    assert percent(decisive) == "3.2%"
    assert hits / 4000 <= 0.035 and decisive <= 0.035
    # The README's worst case is the exact always-decisive judge figure.
    assert f'at 50 cases it gives a false "better" {percent(decisive)} of the time' in README
    assert f"at most {percent(decisive)}" in README
    design = (ROOT / "DESIGN.md").read_text()
    assert f"{hits}/4,000 ({percent(hits / 4000)})" in design
    assert f"exact enumeration gives {percent(decisive)} rounded" in design


def test_multiple_trials_figure_matches_the_one_sided_rate():
    confidence = RunConfig().confidence
    one_sided = (1 - confidence) / 2
    assert percent(one_sided) == "2.5%"
    assert round(100 * (1 - (1 - one_sided) ** 10)) == 22
    assert percent(10 * one_sided) == "25.0%"
    assert "Ten independent tries give about a 22% chance of at least one" in README
    assert f"never more than {percent(10 * one_sided)[:-3]}%" in README


def test_default_case_guidance_matches_the_config_and_split():
    defaults = RunConfig()
    assert defaults.min_pairs == 20 and defaults.holdout == 0.5
    cases = [Case(str(i), {"value": i}) for i in range(40)]
    learning, held = split_cases(cases, defaults.holdout)
    assert len(learning) == len(held) == defaults.min_pairs
    assert len(split_cases(cases[:20], defaults.holdout)[1]) < defaults.min_pairs
    assert "hold out half of your cases by default (`run.holdout`)" in README
    assert "A gate needs 20 cases by default" in README
    assert "a proposal needs about 40 cases before it can be tested" in README
    assert "fewer than 20 cases (`run.min_pairs`, at least 5)" in README


def test_score_gate_makes_no_model_calls_of_its_own():
    assert planned_calls(20, 2, "score")["judge"] == 0


def test_guide_installs_from_pypi():
    guide = (ROOT / "docs/connect-your-agent.md").read_text(encoding="utf-8").lower()
    assert "pip install promptcheckup" in guide
    assert "not on pypi" not in guide and "git+https" not in guide
