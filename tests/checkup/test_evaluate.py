import json

import pytest

from promptcheckup.checkup.evaluate import evaluate, judge_mode, planned_calls, score_mode
from promptcheckup.data import Case
from promptcheckup.llm import LLMError, Usage
from promptcheckup.runner import AgentError, Cache, Replay, Run, function_agent


def cases(count=25):
    return [Case(f"c{i}", {"index": i, "private": "secret"}) for i in range(count)]


def agent(case, prompts):
    return {"value": case["index"] + int(prompts["system"]), "private": "secret"}


def score(case, output):
    return output["value"] - case["index"]


def run_evaluation(scorer, *, new="2", max_calls=400, count=25, function=agent, cache=None):
    return evaluate(
        function_agent(function),
        cases(count),
        {"system": "0"},
        {"system": new},
        scorer,
        samples=2,
        timeout_s=1,
        max_calls=max_calls,
        min_pairs=20,
        cache=cache,
    )


def block(prompt, name):
    return json.loads(prompt.split(f"<{name}>\n")[1].split(f"\n</{name}>")[0])


def content_judge(prompt):
    first = block(prompt, "response_1")["value"]
    second = block(prompt, "response_2")["value"]
    winner = "tie" if first == second else "1" if first > second else "2"
    return json.dumps({"winner": winner, "reason": "Higher value is better."})


def position_judge(prompt):
    return '{"winner": "1", "reason": "First wins."}'


@pytest.mark.parametrize(
    ("new", "decision"), [("2", "better"), ("0", "inconclusive"), ("-2", "worse")]
)
def test_score_mode_real_agent_verdict(new, decision):
    result = run_evaluation(score_mode(score), new=new)

    assert result.verdict.decision == decision
    assert result.scores.per_case == [(case.id, float(new)) for case in cases()]
    assert result.scores.skew_guard is True
    assert result.scores.self_check is None
    assert result.errors == []


def test_budget_exhaustion_downgrades_and_preserves_numbers():
    result = run_evaluation(score_mode(score), max_calls=80)

    assert result.budget_exhausted is True
    assert result.verdict.decision == "inconclusive"
    assert result.verdict.reason == "call budget reached; raise run.max_calls or use fewer cases"
    assert result.verdict.n == 20
    assert result.verdict.mean == result.verdict.ci_low == result.verdict.ci_high == 2


def test_failed_runs_are_reported_and_excluded_from_scores():
    def failing(case, prompts):
        if case["index"] == 0 and prompts["system"] == "2":
            raise ValueError("cannot answer")
        return agent(case, prompts)

    result = run_evaluation(score_mode(score), function=failing)

    assert len(result.errors) == 2
    assert all("cannot answer" in run.error for run in result.errors)
    assert result.scores.per_case == [(case.id, 2.0) for case in cases()[1:]]
    assert result.verdict.decision == "inconclusive"
    assert result.verdict.reason == "2 of 100 runs failed; fix the errors and run again"


def test_failed_majority_cannot_make_survivors_better():
    def failing(case, prompts):
        if prompts["system"] == "1" and case["index"] >= 20:
            raise ValueError("cannot answer")
        return {"value": 0.5 if prompts["system"] == "0" else 1}

    result = evaluate(
        function_agent(failing),
        cases(100),
        {"system": "0"},
        {"system": "1"},
        score_mode(lambda case, output: output["value"]),
        samples=1,
        timeout_s=1,
        max_calls=200,
        min_pairs=20,
        cache=None,
    )
    assert result.verdict.decision == "inconclusive"
    assert result.verdict.n == 20
    assert result.verdict.reason == "80 of 200 runs failed; fix the errors and run again"


@pytest.mark.parametrize("new,decision", [("2", "inconclusive"), ("-2", "worse")])
def test_single_failed_a_run_blocks_only_better(new, decision):
    calls = []

    def failing(case, prompts):
        calls.append(prompts["system"])
        if len(calls) == 1:
            raise ValueError("A failed")
        return agent(case, prompts)

    result = run_evaluation(score_mode(score), function=failing, new=new)
    assert len(result.errors) == 1
    assert result.verdict.decision == decision
    if decision == "inconclusive":
        assert result.verdict.reason == "1 of 100 runs failed; fix the errors and run again"


def test_score_mode_averages_only_complete_successful_pairs():
    replay = Replay(
        [
            Run("c0", "A", 0, {"value": 1}),
            Run("c0", "B", 0, {"value": 5}),
            Run("c0", "A", 1, {"value": 2}),
            Run("c0", "B", 1, {"value": 4}),
            Run("c0", "A", 2, error="failed"),
            Run("c0", "B", 2, {"value": 100}),
        ],
        6,
        False,
    )

    scores = score_mode(score)(cases(1), replay, budget=0, min_pairs=20)

    assert scores.per_case == [("c0", 3.0)]


def test_score_errors_are_loud():
    def broken(case, output):
        raise ValueError("score failed {literal}")

    with pytest.raises(AgentError, match=r"score failed \{literal\}"):
        run_evaluation(score_mode(broken))


def test_evaluate_reuses_agent_cache(tmp_path):
    cache = Cache(tmp_path)
    first = run_evaluation(score_mode(score), cache=cache)
    second = run_evaluation(score_mode(score), cache=cache)

    assert first.verdict == second.verdict
    assert second.replay.calls == 0


def test_content_judge_improvement_passes_with_reliable_self_check():
    result = run_evaluation(judge_mode(content_judge, "Higher value is better."))

    assert result.verdict.decision == "better"
    assert result.scores.per_case == [(case.id, 1.0) for case in cases()]
    assert result.scores.self_check.reliable is True
    assert result.scores.skew_guard is False


def test_position_biased_judge_is_downgraded():
    result = run_evaluation(judge_mode(position_judge, "Higher value is better."))

    assert result.verdict.decision == "inconclusive"
    assert result.verdict.reason.startswith("judge unreliable: ")
    assert result.scores.self_check.reliable is False


def test_gate_reports_existing_ab_order_agreement_without_extra_calls():
    from promptcheckup.checkup.report import gate_report

    seen = []

    def judge(prompt):
        seen.append(prompt)
        return (
            position_judge(prompt)
            if block(prompt, "case_input")["index"] < 5
            else content_judge(prompt)
        )

    result = run_evaluation(judge_mode(judge, "Higher value is better."))
    report = gate_report(result, prompt_hashes={}, version="test")
    assert "Judge order agreement on A vs B: 80%." in report
    assert len(seen) == result.scores.judge_calls == 150


def test_order_agreement_is_absent_without_judged_pairs():
    from promptcheckup.checkup.report import gate_report

    result = run_evaluation(judge_mode(content_judge, "Higher value is better."), max_calls=0)
    assert result.scores.order_agreement is None
    assert "Judge order agreement" not in gate_report(result, prompt_hashes={}, version="test")


def test_redaction_precedes_all_judgments_including_self_check():
    prompts = []

    def judge(prompt):
        prompts.append(prompt)
        return content_judge(prompt)

    def redact(value):
        return {key: val for key, val in value.items() if key != "private"}

    result = run_evaluation(judge_mode(judge, "Higher value is better.", redact=redact))

    assert result.verdict.decision == "better"
    assert len(prompts) == 150
    assert all("secret" not in prompt for prompt in prompts)


def test_judge_errors_propagate():
    def broken(prompt):
        raise LLMError("unavailable")

    with pytest.raises(LLMError, match="unavailable"):
        run_evaluation(judge_mode(broken, "Be correct."))


@pytest.mark.parametrize(("mode", "expected"), [("score", 0), ("judge", 150)])
def test_planned_calls_include_swaps_and_same_prompt_check(mode, expected):
    assert planned_calls(25, 2, mode) == {"agent": 100, "judge": expected}


@pytest.mark.parametrize("cap", [0, 100, 101, 200, 249, 250])
def test_judge_calls_share_total_budget(cap):
    prompts = []

    def judge(prompt):
        prompts.append(prompt)
        return content_judge(prompt)

    result = run_evaluation(judge_mode(judge, "Higher value is better."), max_calls=cap)

    assert result.replay.calls + len(prompts) <= cap
    assert result.scores.judge_calls == len(prompts)
    assert result.budget_exhausted is (cap < 250)
    if cap < 250:
        assert result.verdict.reason == (
            "call budget reached; raise run.max_calls or use fewer cases"
        )


def test_judge_self_check_uses_evaluation_min_pairs():
    result = evaluate(
        function_agent(agent),
        cases(5),
        {"system": "0"},
        {"system": "2"},
        judge_mode(content_judge, "Higher value is better."),
        samples=2,
        timeout_s=1,
        max_calls=50,
        min_pairs=5,
        cache=None,
    )

    assert result.verdict.decision == "better"
    assert result.scores.self_check.reliable is True


def test_judge_usage_counts_are_per_evaluation():
    scorer = judge_mode(content_judge, "Higher value is better.")
    first = run_evaluation(scorer)
    second = run_evaluation(scorer)

    assert first.scores.judge_calls == second.scores.judge_calls == 150
    assert first.scores.prompt_tokens is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "not a score"])
def test_invalid_score_values_raise_agent_error(value):
    with pytest.raises(AgentError, match="scoring.score"):
        run_evaluation(score_mode(lambda case, output: value))


def test_judge_token_counts_and_model_are_snapshots_of_each_evaluation():
    def judge(prompt):
        judge.usage.prompt_tokens += 3
        judge.usage.completion_tokens += 2
        return content_judge(prompt)

    judge.usage = Usage()
    judge.model = "test-model"
    scorer = judge_mode(judge, "Higher value is better.")
    first = run_evaluation(scorer)
    second = run_evaluation(scorer)

    assert first.scores.prompt_tokens == second.scores.prompt_tokens == 450
    assert first.scores.completion_tokens == second.scores.completion_tokens == 300
    assert first.scores.model == "test-model"


def test_judge_self_check_is_cached_for_unchanged_prompt_and_evidence(tmp_path):
    def judge(prompt):
        return content_judge(prompt)

    judge.model = "test-model"
    cache = Cache(tmp_path)
    first = run_evaluation(judge_mode(judge, "Higher value is better.", cache=cache), cache=cache)
    second = run_evaluation(judge_mode(judge, "Higher value is better.", cache=cache), cache=cache)

    assert first.verdict == second.verdict
    assert first.scores.judge_calls == 150
    assert second.scores.judge_calls == 100
    assert second.scores.self_check.calls == 0
    assert second.scores.self_check.reliable is True


def test_judge_self_check_cache_invalidates_for_changed_criteria_model_or_inputs(tmp_path):
    def judge(prompt):
        return content_judge(prompt)

    judge.model = "first-model"
    cache = Cache(tmp_path)
    run_evaluation(judge_mode(judge, "Higher value is better.", cache=cache))
    criteria_change = run_evaluation(judge_mode(judge, "Prefer the higher value.", cache=cache))
    judge.model = "second-model"
    model_change = run_evaluation(judge_mode(judge, "Higher value is better.", cache=cache))
    input_change = run_evaluation(
        judge_mode(judge, "Higher value is better.", cache=cache), count=24
    )

    assert criteria_change.scores.judge_calls == model_change.scores.judge_calls == 150
    assert input_change.scores.judge_calls == 144


def test_judge_self_check_cache_separates_endpoints_serving_the_same_model_name(tmp_path):
    def judge(prompt):
        return content_judge(prompt)

    judge.model = "same-name"
    judge.base_url = "http://first.example/v1"
    cache = Cache(tmp_path)
    run_evaluation(judge_mode(judge, "Higher value is better.", cache=cache))
    judge.base_url = "http://second.example/v1"
    moved = run_evaluation(judge_mode(judge, "Higher value is better.", cache=cache))

    assert moved.scores.judge_calls == 150
    assert moved.scores.self_check.calls == 50


def test_judge_self_check_cache_obeys_ttl_and_recovers_from_damage(tmp_path):
    def judge(prompt):
        return content_judge(prompt)

    judge.model = "test-model"
    now = [0]
    cache = Cache(tmp_path, ttl_hours=1, clock=lambda: now[0])
    scorer = judge_mode(judge, "Higher value is better.", cache=cache)
    run_evaluation(scorer)
    now[0] = 3601
    expired = run_evaluation(scorer)
    for path in tmp_path.glob("*.json"):
        path.write_text('{"created_at": 3601, "output": {"broken": true}}', encoding="utf-8")
    damaged = run_evaluation(scorer)

    assert expired.scores.judge_calls == damaged.scores.judge_calls == 150
