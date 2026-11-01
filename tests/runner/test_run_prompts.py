from promptcheckup.data import Case
from promptcheckup.runner import Cache, function_agent
from promptcheckup.runner.replay import run_prompts


def test_run_prompts_runs_only_a_and_reuses_cache(tmp_path):
    seen = []

    def agent(case, prompts):
        seen.append(case)
        return prompts["system"]

    cases = [Case(str(i), {"i": i}) for i in range(3)]
    cache = Cache(tmp_path)
    first = run_prompts(function_agent(agent), cases, {"system": "x"}, samples=2, cache=cache)
    second = run_prompts(
        function_agent(agent), cases, {"system": "x"}, samples=2, cache=cache, max_calls=0
    )
    assert first.calls == len(first.runs) == len(seen) == 6
    assert {run.prompt_set for run in first.runs} == {"A"}
    assert second.calls == 0 and not second.budget_exhausted
    assert all(run.cached for run in second.runs)


def test_run_prompts_budget_is_not_a_partial_success():
    cases = [Case(str(i), {"i": i}) for i in range(3)]
    result = run_prompts(function_agent(lambda case, prompts: "ok"), cases, {}, max_calls=3)
    assert result.calls == len(result.runs) == 3
    assert result.budget_exhausted


def test_run_prompts_errors_are_recorded_and_never_cached(tmp_path):
    def agent(case, prompts):
        raise ValueError("failure")

    for _ in range(2):
        result = run_prompts(
            function_agent(agent), [Case("c1", {})], {}, samples=1, cache=Cache(tmp_path)
        )
        assert result.calls == 1
        assert "failure" in result.runs[0].error
