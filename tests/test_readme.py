"""The README's claims, recomputed from the committed results, and its release hygiene."""

import csv
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text(encoding="utf-8")
RESULTS = ROOT / "benchmarks/results"
DRAFT_WORDING = ("not on pypi", "pre-release", "in progress", "early development", "coming soon")


def simulation_rate(incident, predicate):
    with (RESULTS / "checkup_simulation.csv").open(encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if row["incident"] == incident]
    return 100 * sum(map(predicate, rows)) / len(rows)


def drifted(row):
    return row["drift_decision"] == "worse"


@pytest.mark.parametrize(
    ("incident", "predicate", "quoted"),
    [
        ("harmful_provider", drifted, "**87.5%**"),
        ("harmless_provider", drifted, "**2.0%**"),
        ("unreviewed_regression", drifted, "**98.5%**"),
        ("real_fix", lambda row: row["proposal_outcome"] == "kept", "**92.5%**"),
        ("useless_fix", lambda row: row["proposal_outcome"] == "rejected", "**95.0%**"),
        ("quiet", drifted, "**2.3%**"),
    ],
)
def test_simulated_year_numbers_match_the_committed_csv(incident, predicate, quoted):
    assert f"**{simulation_rate(incident, predicate):.1f}%**" == quoted
    assert quoted in README


def test_naive_versus_paired_numbers_match_the_committed_csv():
    with (RESULTS / "naive_vs_paired.csv").open(encoding="utf-8") as stream:
        wrong = {row["method"]: float(row["wrong"]) for row in csv.DictReader(stream)}
    assert f"**{100 * wrong['naive (different weeks)']:.1f}%**" in README
    assert wrong["paired replay"] == 0 and "**0%**" in README


def test_example_numbers_match_their_saved_reports():
    haiku = (ROOT / "examples/support-bot/results/report-claude-haiku-5-5.md").read_text()
    trading = (ROOT / "examples/trading/results/report-claude-haiku-5-5.md").read_text()
    assert haiku.splitlines()[0] in README
    tokens = re.search(r"Tokens: (\d+) input, (\d+) output", haiku).groups()
    assert all(f"{int(count):,}" in README for count in tokens)
    mean, low, high = re.search(
        r"\| 60 \| ([+-][\d.]+) \| ([+-][\d.]+) to ([+-][\d.]+) \|", trading
    ).groups()
    assert mean in README and low.lstrip("-") in README and high in README


def test_readme_has_no_draft_wording():
    lowered = README.lower()
    assert [phrase for phrase in DRAFT_WORDING if phrase in lowered] == []


def test_readme_images_and_file_links_work_on_pypi():
    images = re.findall(r'<img src="([^"]+)"|!\[[^\]]*\]\(([^)]+)\)', README)
    sources = [first or second for first, second in images]
    assert sources and all(source.startswith("https://") for source in sources)
    links = re.findall(r"\]\(([^)#][^)]*)\)", README)
    assert all(link.startswith(("https://", "mailto:")) for link in links)
