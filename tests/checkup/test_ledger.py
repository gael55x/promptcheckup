import json
from datetime import datetime

import pytest

from promptcheckup.checkup.evaluate import score_mode
from promptcheckup.checkup.ledger import false_better_expected, record_trial, trials
from promptcheckup.checkup.report import gate_report
from promptcheckup.runner import prompt_hash
from tests.checkup.test_evaluate import run_evaluation, score


def record(directory, *, ids=("b", "a"), variant="one"):
    record_trial(
        directory,
        case_ids=ids,
        prompt_a=prompt_hash({"system": "old"}),
        prompt_b=prompt_hash({"system": variant}),
        decision="better",
        confidence=0.95,
        command="gate",
    )


def test_ledger_counts_distinct_variants_on_same_unordered_case_set(tmp_path):
    assert trials(tmp_path, ["a", "b"]) == 0
    record(tmp_path)
    record(tmp_path, ids=["a", "b"])
    record(tmp_path, variant="two")
    record(tmp_path, ids=["a", "c"], variant="three")
    assert trials(tmp_path, ["b", "a"]) == 2
    assert trials(tmp_path, ["a", "c"]) == 1
    rows = [
        json.loads(line)
        for line in (tmp_path / ".promptcheckup/trials.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 4 and rows[0]["case_set"] == rows[1]["case_set"]
    assert rows[0]["prompt_b"] == prompt_hash({"system": "one"})
    assert rows[0]["command"] == "gate" and rows[0]["confidence"] == 0.95
    assert datetime.fromisoformat(rows[0]["at"]).tzinfo is not None


def test_ledger_skips_damaged_lines_and_preserves_valid_records(tmp_path):
    record(tmp_path)
    path = tmp_path / ".promptcheckup/trials.jsonl"
    with path.open("a") as stream:
        stream.write('broken\n{}\nnull\n[]\n{"case_set": []}\n')
    record(tmp_path, variant="two")
    assert trials(tmp_path, ["a", "b"]) == 2


@pytest.mark.parametrize(
    "count,confidence,expected", [(0, 0.95, 0), (12, 0.95, 0.3), (10, 0.99, 0.05)]
)
def test_false_better_expected_values(count, confidence, expected):
    assert false_better_expected(count, confidence) == pytest.approx(expected)


@pytest.mark.parametrize("count", [0, 1, 2, 12])
def test_gate_report_trials_line_only_for_multiple_variants(count):
    evaluation = run_evaluation(score_mode(score))
    original = gate_report(evaluation, prompt_hashes={}, version="test")
    report = gate_report(evaluation, prompt_hashes={}, version="test", trial_count=count)
    extra = [line for line in report.splitlines() if line.startswith("Trials on these cases:")]
    assert len(extra) == int(count >= 2)
    assert [line for line in report.splitlines() if line and line not in extra] == [
        line for line in original.splitlines() if line
    ]
    if count == 12:
        assert "12 distinct variants" in extra[0] and "about 0.3 false 'better'" in extra[0]
        assert "run.confidence" in extra[0]


def test_gate_headline_uses_requested_confidence():
    report = gate_report(
        run_evaluation(score_mode(score)), prompt_hashes={}, version="test", confidence=0.99
    )
    assert "99% CI" in report.splitlines()[0] and "95%" not in report


def test_ledger_recovers_damaged_tail_without_losing_next_append(tmp_path):
    record(tmp_path)
    path = tmp_path / ".promptcheckup/trials.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"case_set": "truncated\xff')
    record(tmp_path, variant="two")
    assert trials(tmp_path, ["a", "b"]) == 2


def test_ledger_skips_invalid_utf8_inside_an_otherwise_valid_record(tmp_path):
    record(tmp_path)
    path = tmp_path / ".promptcheckup/trials.jsonl"
    damaged = path.read_bytes().replace(b'"prompt_b": "', b'"prompt_b": "\xff')
    with path.open("ab") as stream:
        stream.write(damaged)
    assert trials(tmp_path, ["a", "b"]) == 1
