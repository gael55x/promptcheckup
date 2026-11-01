import json
from copy import deepcopy

import pytest

from promptcheckup.checkup.report import gate_report
from promptcheckup.data import case_id
from promptcheckup.testing import assert_better, assert_no_lookahead, assert_not_worse, gate
from tests.checkup.test_checkup import agent, cases, comparison, score

OLD = {"system": "0"}


def test_assert_better_rejects_failed_runs():
    def failing(case, prompts):
        if case["index"] == 0:
            raise ValueError("failed")
        return agent(case, prompts)

    with pytest.raises(AssertionError, match="runs failed; fix the errors and run again"):
        assert_better(failing, cases(25), OLD, {"system": "2"}, score=score)


@pytest.mark.parametrize("level,decision", [(2, "better"), (-2, "worse"), (0, "inconclusive")])
def test_gate_uses_score_with_plain_function(level, decision):
    result = gate(agent, cases(20), OLD, {"system": str(level)}, score=score)
    assert result.verdict.decision == decision
    assert result.replay.calls == 80
    assert result.scores.self_check is None
    assert not any(run.cached for run in result.replay.runs)


def test_gate_dict_cases_get_stable_ids_and_keep_case_objects():
    inputs = [case.input for case in cases()]
    result = gate(agent, [cases()[0], *inputs[1:]], OLD, {"system": "2"}, score=score, min_pairs=5)
    assert result.verdict.decision == "better"
    assert [key for key, _ in result.scores.per_case] == ["c0", *map(case_id, inputs[1:])]


def test_gate_judge_uses_criteria_swaps_and_self_check():
    seen = []

    def judge(prompt):
        seen.append(prompt)
        return json.dumps(comparison(prompt))

    result = gate(
        agent, cases(), OLD, {"system": "2"}, judge=judge, criteria="Higher is better.", min_pairs=5
    )
    assert result.verdict.decision == "better"
    assert result.scores.self_check.reliable
    assert result.scores.judge_calls == len(seen) == 30
    assert all("Higher is better." in prompt for prompt in seen)


@pytest.mark.parametrize("options", [{}, {"score": score, "judge": lambda prompt: "{}"}])
def test_gate_requires_exactly_one_scorer(options):
    with pytest.raises(ValueError) as error:
        gate(agent, cases(), OLD, OLD, **options)
    assert str(error.value) == "provide exactly one of score or judge"


@pytest.mark.parametrize("helper", [assert_better, assert_not_worse])
def test_assertions_return_successful_evaluation(helper):
    result = helper(agent, cases(), OLD, {"system": "2"}, score=score, min_pairs=5)
    assert result.verdict.decision == "better"


def test_assert_not_worse_accepts_inconclusive():
    result = assert_not_worse(agent, cases(), OLD, OLD, score=score, min_pairs=5)
    assert result.verdict.decision == "inconclusive"


@pytest.mark.parametrize(
    "helper,level", [(assert_better, -2), (assert_better, 0), (assert_not_worse, -2)]
)
def test_assertion_failure_shows_the_report_headline_and_stats_table(helper, level):
    new = {"system": str(level)}
    result = gate(agent, cases(), OLD, new, score=score, min_pairs=5, confidence=0.99)
    report = gate_report(result, prompt_hashes={}, version="test", confidence=0.99)
    headline, table = report.split("\n\n")[:2]
    with pytest.raises(AssertionError) as error:
        helper(agent, cases(), OLD, new, score=score, min_pairs=5, confidence=0.99)
    assert str(error.value) == headline + "\n\n" + table
    assert "| n | Mean |" in str(error.value)  # the stats keep their column headers


def test_gate_forwards_budget_and_samples_without_reusing_outputs():
    result = gate(
        agent, cases(), OLD, {"system": "2"}, score=score, samples=3, min_pairs=5, max_calls=19
    )
    assert result.replay.calls == 18  # the shared replay starts only affordable complete pairs
    assert max(run.sample for run in result.replay.runs) == 2
    assert result.budget_exhausted and result.verdict.decision == "inconclusive"


@pytest.fixture
def history():
    return [
        {"time": index, "price": 10.0 + index, "volume": index, "open": True, "name": "A"}
        for index in range(4)
    ]


def test_no_lookahead_passes_honest_view_without_mutating_history(history):
    before = deepcopy(history)
    assert_no_lookahead(
        lambda rows, t: [row for row in rows if row["time"] <= t], history, [0, 1, 2]
    )
    assert history == before


def test_no_lookahead_fails_at_first_leaking_time_without_mutating_history(history):
    before = deepcopy(history)
    with pytest.raises(AssertionError) as error:
        assert_no_lookahead(lambda rows, t: rows[min(t + 1, 3)]["price"], history, [3, 1, 0])
    assert str(error.value) == "view(1) changed when only data after 1 changed: it reads the future"
    assert history == before


def test_no_lookahead_rejects_future_metadata_via_row_removal(history):
    with pytest.raises(AssertionError, match="reads the future"):
        assert_no_lookahead(
            lambda rows, t: [(row["time"], row["open"], row["name"]) for row in rows],
            history,
            [0, 1],
        )


def test_no_lookahead_catches_comparison_between_future_prices():
    history = [{"time": 0, "price": 1}, {"time": 1, "price": 10}, {"time": 2, "price": 10.01}]
    with pytest.raises(AssertionError, match="reads the future"):
        assert_no_lookahead(lambda rows, t: rows[-1]["price"] > rows[-2]["price"], history, [0])


def test_no_lookahead_catches_future_row_dependency_even_without_numbers():
    history = [{"time": 0, "status": "closed"}, {"time": 1, "status": "open"}]
    with pytest.raises(AssertionError, match="reads the future"):
        assert_no_lookahead(lambda rows, t: rows[1]["status"], history, [0])


def test_no_lookahead_random_changes_are_seeded_and_independent(history):
    calls = []

    def view(rows, time):
        calls.append(deepcopy(rows))
        return [row for row in rows if row["time"] <= time]

    assert_no_lookahead(view, history, [0])
    first = deepcopy(calls)
    calls.clear()
    assert_no_lookahead(view, history, [0])
    assert calls == first
    assert any(len(rows) == 1 for rows in calls)
    random_rows = [rows for rows in calls if len(rows) == len(history) and rows != history][-1]
    ratios = [
        row["price"] / old["price"] for row, old in zip(random_rows[1:], history[1:], strict=True)
    ]
    assert len(set(ratios)) == len(ratios)
    assert all(0.5 <= ratio <= 1.5 for ratio in ratios)


def test_no_lookahead_perturbs_all_future_numeric_columns_only(history):
    seen = []

    def view(rows, t):
        seen.append(deepcopy(rows))
        return rows[: t + 1]

    assert_no_lookahead(view, history, [1])
    perturbed = next(rows for rows in seen if rows != history)
    assert perturbed[:2] == history[:2]
    assert [(row["price"], row["volume"]) for row in perturbed[2:]] == [
        (row["price"] * 1.37 + 1, row["volume"] * 1.37 + 1) for row in history[2:]
    ]


def test_no_lookahead_accepts_custom_sortable_time_key():
    rows = [{"date": "2026-10-01", "value": 0}, {"date": "2026-10-02", "value": 0}]
    with pytest.raises(AssertionError) as error:
        assert_no_lookahead(
            lambda rows, t: rows[-1]["value"], rows, ["2026-10-01"], time_key="date"
        )
    assert str(error.value) == (
        "view('2026-10-01') changed when only data after '2026-10-01' changed: it reads the future"
    )
