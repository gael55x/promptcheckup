"""The trading example's published results, checked against its committed recordings."""

import importlib
import sys
from pathlib import Path

import pytest

from promptcheckup.checkup.evaluate import evaluate, score_mode
from promptcheckup.data import load_cases
from promptcheckup.runner import function_agent

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/trading"
MODULES = ("market", "pnl", "trader", "check_policies")


@pytest.fixture
def committed(monkeypatch):
    """The example exactly as shipped: committed recordings, default model, replay only."""
    monkeypatch.syspath_prepend(str(EXAMPLE))
    for name in ("TRADER_RECORD", "TRADER_RECORDINGS", "TRADER_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name in MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    return {name: importlib.import_module(name) for name in MODULES}


def recorded_gate(modules):
    prompts = [
        {"prompts/policy.md": (EXAMPLE / path).read_text(encoding="utf-8")}
        for path in ("prompts/old-policy.md", "prompts/policy.md")
    ]
    return evaluate(
        function_agent(modules["trader"].run),
        load_cases(["cases/cases.jsonl"], EXAMPLE),
        *prompts,
        score_mode(modules["pnl"].score),
        samples=1,
        timeout_s=5,
        max_calls=200,
        min_pairs=20,
        cache=None,
    )


def test_recorded_model_follows_both_policies(committed):
    assert committed["check_policies"].compliance("A") == (60, 60)
    assert committed["check_policies"].compliance("B") == (60, 60)


def test_gate_on_the_recordings_matches_the_saved_report(committed):
    verdict = recorded_gate(committed).verdict
    saved = (EXAMPLE / "results/report-claude-haiku-5-5.md").read_text(encoding="utf-8")

    assert verdict.decision == "inconclusive"
    assert (verdict.wins, verdict.losses, verdict.ties) == (21, 11, 28)
    row = f"| 60 | {verdict.mean:+.2f} | {verdict.ci_low:+.2f} to {verdict.ci_high:+.2f} |"
    assert row in saved and row == "| 60 | +15.35 | -10.64 to +41.34 |"


def test_rules_on_every_ticker_day_show_no_reliable_edge(committed):
    verdict = committed["check_policies"].market_check()
    summary = (verdict.decision, verdict.n, round(verdict.mean, 2))
    assert summary == ("inconclusive", 2085, 1.37)
    assert (round(verdict.ci_low, 2), round(verdict.ci_high, 2)) == (-2.61, 5.36)


def test_readme_quotes_only_numbers_these_checks_produce():
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    for quoted in ("+15.35", "-10.64 to +41.34", "21, lost 11, tied 28", "60 of 60", "2085"):
        assert quoted in readme
    assert "+1.37" in readme and "-2.61 to +5.36" in readme
