"""Check the recorded model against the policies as code, then on every ticker-day.

No model calls. Two questions:
1. Did the recorded model follow each policy? Compare its positions with the rules.
2. Is policy B really better on this market? Apply both rules to every day that has
   a 20-day lookback and a next day, and run the same gate.
"""

from pathlib import Path

import market
import pnl
import trader

from promptcheckup.data import load_cases
from promptcheckup.stats import Verdict, decide

ROOT = Path(__file__).resolve().parent
TOLERANCE = 0.02  # a recorded position this close to the rule counts as following it
POLICY_FILES = {"A": "prompts/old-policy.md", "B": "prompts/policy.md"}


def policy_a(stats: dict) -> float:
    """Follow the trend, sized by the 5-day return."""
    size = min(1.0, abs(stats["return_5d"]) / 0.05)
    return size if stats["return_5d"] > 0 else -size if stats["return_5d"] < 0 else 0.0


def policy_b(stats: dict) -> float:
    """Policy A, flat in high volatility or after a big drop, halved after a 3% drop."""
    if stats["volatility_20d"] > 0.02 or stats["last_day_return"] < -0.04:
        return 0.0
    position = policy_a(stats)
    return position / 2 if stats["worst_day_return"] <= -0.03 else position


RULES = {"A": policy_a, "B": policy_b}


def compliance(name: str) -> tuple[int, int]:
    """How many recorded positions follow the policy's rule, out of all cases."""
    prompts = {"prompts/policy.md": (ROOT / POLICY_FILES[name]).read_text(encoding="utf-8")}
    cases = load_cases(["cases/cases.jsonl"], ROOT)
    followed = sum(
        abs(
            trader.run(case.input, prompts)["position"]
            - RULES[name](trader.market_window(case.input))
        )
        <= TOLERANCE
        for case in cases
    )
    return followed, len(cases)


def all_ticker_days() -> list[dict]:
    return [{"ticker": ticker, "day": day} for ticker in market.TICKERS for day in range(20, 159)]


def rule_difference(case: dict) -> float:
    stats = trader.market_window(case)
    return pnl.score(case, {"position": policy_b(stats)}) - pnl.score(
        case, {"position": policy_a(stats)}
    )


def market_check() -> Verdict:
    """The gate on every ticker-day, with both policies applied exactly as written."""
    return decide([rule_difference(case) for case in all_ticker_days()])


def report() -> str:
    counts = {name: compliance(name) for name in RULES}
    lines = [
        f"Policy {name}: {followed} of {total} recorded positions follow the rule"
        for name, (followed, total) in counts.items()
    ]
    verdict = market_check()
    lines.append(
        f"All {verdict.n} ticker-days, rules applied exactly: {verdict.decision}, "
        f"mean {verdict.mean:+.2f} bp, 95% CI {verdict.ci_low:+.2f} to {verdict.ci_high:+.2f}"
    )
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
