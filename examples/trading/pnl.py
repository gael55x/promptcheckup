"""One day's position P&L, in basis points, after the full round-trip cost."""

import csv
from pathlib import Path

from promptcheckup.errors import errors_as
from promptcheckup.runner import AgentError

COST = 0.001  # ten basis points per unit of absolute position, once per case
OUTPUT_RULES = (
    (lambda output: isinstance(output, dict), "output must contain a numeric position"),
    (lambda output: type(output.get("position")) in (int, float), "position must be a number"),
    (lambda output: -1 <= output["position"] <= 1, "position must be finite and within [-1, 1]"),
)


def load_prices(path: Path) -> dict[tuple[str, int], float]:
    with path.open(encoding="utf-8", newline="") as stream:
        return {
            (row["ticker"], int(row["day"])): float(row["close"]) for row in csv.DictReader(stream)
        }


PRICES = load_prices(Path(__file__).resolve().parent / "data/market.csv")


def position(output) -> float:
    for predicate, message in OUTPUT_RULES:
        if not predicate(output):
            raise AgentError(message)
    return float(output["position"])


def score(case: dict, output) -> float:
    size = position(output)
    with errors_as(AgentError, {KeyError: "no next-day prices for this case"}):
        today = PRICES[case["ticker"], case["day"]]
        tomorrow = PRICES[case["ticker"], case["day"] + 1]
    return float(10000 * (size * (tomorrow / today - 1) - COST * abs(size)))
