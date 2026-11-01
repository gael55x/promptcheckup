"""Seeded fictional prices and cases. Regenerate with python market.py."""

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path

from promptcheckup.data.cases import case_id

ROOT = Path(__file__).resolve().parent
SEED = 20261101
CASE_SEED = 0
TICKERS = tuple(f"SYN{number:02}" for number in range(1, 16))
DAYS = 160
STRESSED_SPELLS = ((40, 60), (100, 120))  # inclusive start, exclusive end


@dataclass(frozen=True)
class Ticker:
    name: str
    drift: float
    volatility: float
    initial_close: float


@dataclass(frozen=True)
class Close:
    ticker: str
    day: int
    close: float


def stressed(day: int) -> bool:
    return any(start <= day < end for start, end in STRESSED_SPELLS)


def daily_return(rng: random.Random, ticker: Ticker, day: int, previous: float) -> float:
    if rng.random() < 0.01:
        return -rng.uniform(0.06, 0.12)
    multiplier = 3.0 if stressed(day) else 1.0
    momentum = 0.04 if stressed(day) else 0.18
    trend = momentum * max(-0.03, min(0.03, previous))
    move = ticker.drift + trend + rng.gauss(0, ticker.volatility * multiplier)
    return max(-0.055, min(0.055, move))


def ticker_closes(rng: random.Random, ticker: Ticker) -> list[Close]:
    price, previous = ticker.initial_close, 0.0
    rows = [Close(ticker.name, 0, price)]
    for day in range(1, DAYS):
        previous = daily_return(rng, ticker, day, previous)
        price *= 1 + previous
        rows.append(Close(ticker.name, day, price))
    return rows


def generate_market(seed: int = SEED) -> list[Close]:
    rng = random.Random(seed)
    tickers = [
        Ticker(name, rng.uniform(-0.0001, 0.0006), rng.uniform(0.006, 0.012), rng.uniform(60, 140))
        for name in TICKERS
    ]
    return [row for ticker in tickers for row in ticker_closes(rng, ticker)]


def write_market(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("ticker", "day", "close"))
        writer.writerows((row.ticker, row.day, f"{row.close:.6f}") for row in generate_market())


def generate_cases(seed: int = CASE_SEED) -> list[dict]:
    rng = random.Random(seed)
    inputs = [
        {"ticker": ticker, "day": day}
        for ticker in TICKERS
        for day in sorted(rng.sample(range(30, 159), 4))
    ]
    return [{"id": case_id(case), "input": case, "source": "synthetic"} for case in inputs]


def write_cases(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in generate_cases()), encoding="utf-8")


if __name__ == "__main__":
    write_market(ROOT / "data/market.csv")
    write_cases(ROOT / "cases/cases.jsonl")
