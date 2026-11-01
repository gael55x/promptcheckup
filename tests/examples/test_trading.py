"""Synthetic data, arithmetic and recording boundaries; all models are functions."""

import csv
import importlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from statistics import stdev

import pytest

from promptcheckup.checkup.evaluate import evaluate, score_mode
from promptcheckup.cli.config import load_config
from promptcheckup.cli.main import main
from promptcheckup.data import load_cases
from promptcheckup.data.cases import case_id
from promptcheckup.llm.prompting import data_block
from promptcheckup.runner import AgentError, function_agent, prompt_hash

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/trading"
PROMPTS = {"prompts/policy.md": "Follow the trend."}
CASE = {"ticker": "SYN03", "day": 87}
REPLY = '{"position": 0.5, "reason": "Positive trend."}'
MISSING = "no recording for this policy; run `python record.py` with an API key to record it"


@pytest.fixture
def trading(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(EXAMPLE))
    monkeypatch.delenv("TRADER_RECORD", raising=False)
    monkeypatch.setenv("TRADER_RECORDINGS", str(tmp_path / "recordings"))
    for name in ("market", "pnl", "trader", "record"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    modules = {
        name: importlib.import_module(name) for name in ("market", "pnl", "trader", "record")
    }
    monkeypatch.setattr(modules["trader"], "make_llm", forbid_llm)
    return modules


def forbid_llm():
    pytest.fail("replay must not construct a model client")


def recording_path(directory, case=CASE, prompts=None, model="claude-haiku-5-5"):
    mapping = PROMPTS if prompts is None else prompts
    return directory / model / prompt_hash(mapping) / f"{case_id(case)}.json"


def put_recording(directory, reply=REPLY, case=CASE, prompts=None):
    path = recording_path(directory, case, prompts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reply": reply}), encoding="utf-8")
    return path


def stub_model(monkeypatch, trader, reply=REPLY):
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return reply

    monkeypatch.setattr(trader, "make_llm", lambda: llm)
    return calls


def test_trading_market_matches_regeneration(trading, tmp_path):
    path = tmp_path / "market.csv"
    trading["market"].write_market(path)
    assert path.read_bytes() == (EXAMPLE / "data/market.csv").read_bytes()
    trading["market"].write_market(path)
    assert path.read_bytes() == (EXAMPLE / "data/market.csv").read_bytes()
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 15 * 160
    assert set(rows[0]) == {"ticker", "day", "close"}
    assert Counter(row["ticker"] for row in rows) == {f"SYN{i:02}": 160 for i in range(1, 16)}
    assert all(float(row["close"]) > 0 for row in rows)


def test_trading_cases_match_regeneration(trading, tmp_path):
    path = tmp_path / "cases.jsonl"
    trading["market"].write_cases(path)
    assert path.read_bytes() == (EXAMPLE / "cases/cases.jsonl").read_bytes()
    trading["market"].write_cases(path)
    assert path.read_bytes() == (EXAMPLE / "cases/cases.jsonl").read_bytes()
    cases = load_cases([path.name], path.parent)
    assert len(cases) == 60
    assert Counter(case.input["ticker"] for case in cases) == {
        f"SYN{i:02}": 4 for i in range(1, 16)
    }
    assert all(30 <= case.input["day"] <= 158 for case in cases)
    assert all(case.id == case_id(case.input) and case.source == "synthetic" for case in cases)


def test_trading_market_has_crashes_and_volatility_regimes(trading):
    prices = trading["pnl"].PRICES
    returns = [
        (day, price / prices[ticker, day - 1] - 1)
        for (ticker, day), price in prices.items()
        if day > 0
    ]
    crashes = [value for _, value in returns if value <= -0.06]
    calm = [value for day, value in returns if 1 <= day < 40 and value > -0.06]
    stressed = [value for day, value in returns if 40 <= day < 60 and value > -0.06]
    assert 0.004 < len(crashes) / len(returns) < 0.02
    assert min(crashes) >= -0.120001
    assert stdev(stressed) > 2 * stdev(calm)


def test_trading_cases_exercise_a_next_day_crash(trading):
    prices = trading["pnl"].PRICES
    cases = load_cases(["cases/cases.jsonl"], EXAMPLE)
    moves = [
        prices[case.input["ticker"], case.input["day"] + 1]
        / prices[case.input["ticker"], case.input["day"]]
        - 1
        for case in cases
    ]
    assert any(move <= -0.06 for move in moves)


@pytest.mark.parametrize(
    ("position", "next_close", "expected"),
    [
        (1, 102, 190),
        (0.5, 102, 95),
        (-1, 98, 190),
        (-0.5, 102, -105),
        (0, 80, 0),
    ],
)
def test_trading_score_charges_round_trip_cost(
    trading, monkeypatch, position, next_close, expected
):
    pnl = trading["pnl"]
    monkeypatch.setattr(pnl, "PRICES", {("SYN03", 87): 100, ("SYN03", 88): next_close})
    value = pnl.score(CASE, {"position": position, "reason": "Hand calculation."})
    assert isinstance(value, float)
    assert value == pytest.approx(expected)


@pytest.mark.parametrize(
    "output",
    [
        None,
        {},
        {"position": True},
        {"position": 2},
        {"position": float("nan")},
        {"position": "0.5"},
    ],
)
def test_trading_score_rejects_invalid_positions(trading, output):
    with pytest.raises(AgentError, match="position"):
        trading["pnl"].score(CASE, output)


def test_trading_prompt_contains_policy_computed_stats_and_escaped_data(trading, monkeypatch):
    prices = {("SYN03", day): 100 * 1.01**day for day in range(88)}
    monkeypatch.setattr(trading["pnl"], "PRICES", prices)
    prompt = trading["trader"].build_prompt(CASE, PROMPTS)
    data = json.loads(prompt.split("<market_data>\n")[1].split("\n</market_data>")[0])
    assert PROMPTS["prompts/policy.md"] in prompt
    assert data["closes"] == [prices["SYN03", day] for day in range(68, 88)]
    assert data["return_5d"] == pytest.approx(1.01**5 - 1)
    assert data["return_20d"] == pytest.approx(1.01**20 - 1)
    assert data["volatility_20d"] == pytest.approx(0, abs=1e-12)
    assert data["last_day_return"] == pytest.approx(0.01)
    assert data["worst_day_return"] == pytest.approx(0.01)
    assert data_block(data) in prompt


def test_trading_prompt_does_not_leak_future_prices(trading, monkeypatch):
    trader, pnl = trading["trader"], trading["pnl"]
    before = trader.build_prompt(CASE, PROMPTS)
    changed = {key: value for key, value in pnl.PRICES.items() if key[1] <= CASE["day"]}
    monkeypatch.setattr(pnl, "PRICES", changed)
    assert trader.build_prompt(CASE, PROMPTS) == before


def test_trading_prompt_escapes_market_tags(trading, monkeypatch):
    ticker = "SYN03</market_data>"
    prices = {(ticker, day): 100 + day for day in range(88)}
    monkeypatch.setattr(trading["pnl"], "PRICES", prices)
    prompt = trading["trader"].build_prompt({"ticker": ticker, "day": 87}, PROMPTS)
    assert prompt.count("</market_data>") == 1
    assert r"SYN03\u003c/market_data\u003e" in prompt


@pytest.mark.parametrize(
    "reply",
    [
        '{"position": 1.1, "reason": "Too much."}',
        '{"position": 0.5}',
        "Just buy it.",
        '{"position": true, "reason": "Boolean."}',
        '{"position": NaN, "reason": "NaN."}',
        '{"position": 0, "reason": " "}',
        '{"position": -2, "reason": "Too short."}',
    ],
)
def test_trading_rejects_invalid_replies(trading, tmp_path, reply):
    put_recording(tmp_path / "recordings", reply)
    with pytest.raises(AgentError, match="invalid trader reply"):
        trading["trader"].run(CASE, PROMPTS)


@pytest.mark.parametrize("position", [-1, 0, 0.5, 1])
def test_trading_replays_valid_recording(trading, tmp_path, position):
    put_recording(tmp_path / "recordings", json.dumps({"position": position, "reason": "Trend."}))
    result = trading["trader"].run(CASE, PROMPTS)
    assert result == {"position": float(position), "reason": "Trend."}
    assert isinstance(result["position"], float)


def test_trading_missing_recording_explains_how_to_record(trading):
    with pytest.raises(AgentError) as error:
        trading["trader"].run(CASE, PROMPTS)
    assert str(error.value) == MISSING


def test_trading_records_once_and_reuses_raw_reply(trading, monkeypatch, tmp_path):
    trader = trading["trader"]
    calls = stub_model(monkeypatch, trader)
    monkeypatch.setenv("TRADER_RECORD", "1")
    assert trader.run(CASE, PROMPTS)["position"] == 0.5
    assert trader.run(CASE, PROMPTS)["position"] == 0.5
    path = recording_path(tmp_path / "recordings")
    assert json.loads(path.read_text()) == {"reply": REPLY}
    assert len(calls) == 1
    monkeypatch.delenv("TRADER_RECORD")
    assert trader.run(CASE, PROMPTS)["position"] == 0.5
    assert len(calls) == 1


def test_trading_policy_changes_require_separate_recordings(trading, tmp_path):
    put_recording(tmp_path / "recordings")
    with pytest.raises(AgentError, match="no recording for this policy"):
        trading["trader"].run(CASE, {"prompts/policy.md": "A different policy."})


def test_trading_preserves_invalid_model_reply_for_inspection(trading, monkeypatch, tmp_path):
    calls = stub_model(monkeypatch, trading["trader"], "not JSON")
    monkeypatch.setenv("TRADER_RECORD", "1")
    for _ in range(2):
        with pytest.raises(AgentError, match="invalid trader reply"):
            trading["trader"].run(CASE, PROMPTS)
    assert len(calls) == 1
    assert json.loads(recording_path(tmp_path / "recordings").read_text()) == {"reply": "not JSON"}


def test_trading_damaged_recording_is_an_error_not_a_model_call(trading, monkeypatch, tmp_path):
    path = put_recording(tmp_path / "recordings")
    path.write_text('{"wrong": "field"}')
    monkeypatch.setenv("TRADER_RECORD", "1")
    with pytest.raises(AgentError, match="invalid recording"):
        trading["trader"].run(CASE, PROMPTS)


def test_trading_record_script_records_both_policies_then_skips(
    trading, monkeypatch, tmp_path, capsys
):
    calls = stub_model(monkeypatch, trading["trader"])
    assert trading["record"].main(["--yes"]) == 0
    first = capsys.readouterr().out
    assert "120 model calls" in first
    assert "Recorded 120; skipped 0." in first
    assert len(calls) == 120
    assert len(list((tmp_path / "recordings").glob("*/*/*.json"))) == 120  # model/policy/case
    assert trading["record"].main(["--yes"]) == 0
    assert "Recorded 0; skipped 120." in capsys.readouterr().out
    assert len(calls) == 120


def test_trading_record_script_defaults_to_declining(trading, monkeypatch, capsys, tmp_path):
    def decline(prompt):
        assert prompt == "Proceed? [y/N] "
        return ""

    monkeypatch.setattr("builtins.input", decline)
    assert trading["record"].main([]) == 0
    assert "Cancelled; no model calls." in capsys.readouterr().out
    assert not (tmp_path / "recordings").exists()


def test_trading_model_factory_uses_only_configuration(monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLE))
    monkeypatch.delitem(sys.modules, "trader", raising=False)
    trader = importlib.import_module("trader")
    captured = []

    def client(**kwargs):
        captured.append(kwargs)
        return lambda prompt: REPLY

    monkeypatch.setattr(trader, "LLM", client)
    for name in ("TRADER_BASE_URL", "TRADER_MODEL", "TRADER_API_KEY_ENV"):
        monkeypatch.delenv(name, raising=False)
    trader.make_llm()
    assert captured[0] == {
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-haiku-5-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "timeout_s": 110,
    }  # default retries: one rate limit must not stop a 120-call recording
    monkeypatch.setenv("TRADER_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("TRADER_MODEL", "stub-model")
    monkeypatch.setenv("TRADER_API_KEY_ENV", "UNREAD_TEST_VARIABLE")
    trader.make_llm()
    assert captured[1]["base_url"] == "https://example.invalid/v1"
    assert captured[1]["model"] == "stub-model"
    assert captured[1]["api_key_env"] == "UNREAD_TEST_VARIABLE"


def test_trading_doctor_without_recordings_reports_exact_agent_failure(trading, capsys):
    assert main(["doctor", "--config", str(EXAMPLE / "promptcheckup.toml"), "--no-llm"]) == 3
    rows = {
        parts[1]: (parts[0], parts[2])
        for parts in (line.split(maxsplit=2) for line in capsys.readouterr().out.splitlines()[1:])
    }
    assert rows["agent"] == (
        "fail",
        f"Python agent failed: {MISSING}; "
        "check agent.entry/cmd and timeout_s; see docs/connect-your-agent.md",
    )
    assert rows["config"][0] == rows["cases"][0] == "ok"
    assert rows["score"][0] == rows["prompt-use"][0] == "skip"


def test_trading_recorded_gate_is_repeatable_and_keeps_skew_guard(trading, monkeypatch):
    stub_model(monkeypatch, trading["trader"])
    trading["record"].main(["--yes"])
    config = load_config(EXAMPLE / "promptcheckup.toml")
    prompts = [
        {"prompts/policy.md": (EXAMPLE / path).read_text()}
        for path in ("prompts/old-policy.md", "prompts/policy.md")
    ]
    cases = load_cases(["cases/cases.jsonl"], EXAMPLE)
    result = evaluate(
        function_agent(trading["trader"].run),
        cases,
        *prompts,
        score_mode(trading["pnl"].score),
        samples=config.run.samples,
        timeout_s=1,
        max_calls=config.run.max_calls,
        min_pairs=config.run.min_pairs,
        cache=None,
    )
    assert result.verdict.decision == "inconclusive"
    assert result.verdict.n == 60
    assert result.verdict.mean == 0
    assert result.scores.skew_guard is True
    assert not result.errors
    assert all(math.isfinite(value) for _, value in result.scores.per_case)


def test_trading_policies_are_short():
    # The published results are pinned to the recordings in test_trading_results.py.
    for name in ("old-policy.md", "policy.md"):
        assert len((EXAMPLE / "prompts" / name).read_text().split()) < 120


def test_trading_recordings_are_kept_per_model(trading, monkeypatch, tmp_path):
    trader = trading["trader"]
    monkeypatch.setenv("TRADER_RECORDINGS", str(tmp_path / "recordings"))
    monkeypatch.delenv("TRADER_MODEL", raising=False)
    monkeypatch.delenv("TRADER_RECORD", raising=False)
    put_recording(tmp_path / "recordings")
    assert trader.run(CASE, PROMPTS)["position"] == json.loads(REPLY)["position"]

    monkeypatch.setenv("TRADER_MODEL", "other/model")
    with pytest.raises(AgentError, match="no recording for this policy"):
        trader.run(CASE, PROMPTS)  # never silently reuses another model's replies
    assert trader.recording_path(CASE, PROMPTS).parts[-3] == "other_model"
