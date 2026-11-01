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
    assert (verdict.wins, verdict.losses, verdict.ties) == (26, 13, 21)
    row = f"| 60 | {verdict.mean:+.2f} | {verdict.ci_low:+.2f} to {verdict.ci_high:+.2f} |"
    assert row in saved and row == "| 60 | +12.76 | -11.04 to +36.57 |"


def test_rules_on_every_ticker_day_show_no_reliable_edge(committed):
    verdict = committed["check_policies"].market_check()
    summary = (verdict.decision, verdict.n, round(verdict.mean, 2))
    assert summary == ("inconclusive", 2085, 1.37)
    assert (round(verdict.ci_low, 2), round(verdict.ci_high, 2)) == (-2.61, 5.36)


def test_readme_quotes_only_numbers_these_checks_produce():
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    for quoted in ("+12.76", "-11.04 to +36.57", "26, lost 13, tied 21", "60 of 60", "2085"):
        assert quoted in readme
    assert "+1.37" in readme and "-2.61 to +5.36" in readme


def test_the_model_never_sees_a_price_after_the_decision_day(committed, monkeypatch):
    """Scramble every price after each decision day: the model's prompt must not change."""
    trader, pnl, market = committed["trader"], committed["pnl"], committed["market"]
    prompts = {"prompts/policy.md": "Follow the trend."}
    days = [{"ticker": t, "day": d} for t in market.TICKERS for d in range(20, 159)]
    honest = {(c["ticker"], c["day"]): trader.build_prompt(c, prompts) for c in days}
    original = dict(pnl.PRICES)
    for case in days:
        future = {
            key: price * 1.37 if key[0] == case["ticker"] and key[1] > case["day"] else price
            for key, price in original.items()
        }
        monkeypatch.setattr(pnl, "PRICES", future)
        assert trader.build_prompt(case, prompts) == honest[case["ticker"], case["day"]]


def test_the_window_ends_on_the_decision_day_and_the_score_uses_only_the_next_day(committed):
    trader, pnl = committed["trader"], committed["pnl"]
    case = {"ticker": "SYN03", "day": 87}
    window = trader.market_window(case)
    expected = [pnl.PRICES["SYN03", day] for day in range(68, 88)]
    assert window["closes"] == expected  # the last 20 closes, ending on day 87
    today, tomorrow = pnl.PRICES["SYN03", 87], pnl.PRICES["SYN03", 88]
    assert pnl.score(case, {"position": 1.0}) == 10000 * (tomorrow / today - 1 - 0.001)


def test_cases_follow_a_fixed_rule_chosen_without_looking_at_prices(committed):
    """Four evenly spaced days per ticker, staggered by ticker: no seed to pick, no outcome."""
    market = committed["market"]
    expected = [
        {"ticker": ticker, "day": day}
        for index, ticker in enumerate(market.TICKERS)
        for day in sorted(30 + (step * 32 + index * 8) % 128 for step in range(4))
    ]
    assert [row["input"] for row in market.generate_cases()] == expected
    assert all(case["day"] + 1 < market.DAYS for case in expected)  # every case has a next day
