import time

import pytest

from promptcheckup.checkup.evaluate import evaluate, score_mode
from promptcheckup.data import Case
from promptcheckup.runner import Cache, function_agent, prompt_hash, replay, run_prompts

PROMPTS_A = {"system": "Answer briefly."}
PROMPTS_B = {"system": "Answer briefly and cite the policy."}


def test_mutating_input_cannot_make_identical_prompts_better():
    def agent(case, prompts):
        case["messages"].append("reply")
        return len(case["messages"])

    cases = [Case(str(i), {"messages": []}) for i in range(20)]
    result = evaluate(
        function_agent(agent),
        cases,
        PROMPTS_A,
        PROMPTS_A,
        score_mode(lambda case, output: output),
        samples=2,
        timeout_s=1,
        max_calls=80,
        min_pairs=20,
        cache=None,
    )
    assert result.verdict.decision == "inconclusive"
    assert result.verdict.mean == 0
    assert all(run.output == 1 for run in result.replay.runs)
    assert all(case.input == {"messages": []} for case in cases)


def test_mutating_prompts_cannot_change_another_run():
    def agent(case, prompts):
        original = prompts["system"]
        prompts["system"] = "changed"
        return original

    prompts = dict(PROMPTS_A)
    result = replay(function_agent(agent), make_cases(2), prompts, prompts)
    assert all(run.output == PROMPTS_A["system"] for run in result.runs)
    assert prompts == PROMPTS_A


def test_shared_mutable_outputs_are_copied_before_next_call(tmp_path):
    shared = {"answers": []}

    def agent(case, prompts):
        shared["answers"].append(prompts["system"])
        return shared

    cache = Cache(tmp_path)
    result = replay(
        function_agent(agent), make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache
    )
    assert result.runs[0].output == {"answers": [PROMPTS_A["system"]]}
    shared["answers"].clear()
    assert len(result.runs[1].output["answers"]) == 2
    cached = replay(
        function_agent(agent), make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache
    )
    assert [run.output for run in cached.runs] == [run.output for run in result.runs]


def test_half_cached_pair_reruns_both_sides_after_environment_change(tmp_path):
    environment = [0]
    seen = []

    def agent(case, prompts):
        seen.append(prompts["system"])
        return environment[0] - int(prompts["system"])

    cache = Cache(tmp_path)
    cases = make_cases(20)
    old, new = {"system": "0"}, {"system": "1"}
    run_prompts(function_agent(agent), cases, old, samples=1, cache=cache)
    environment[0] = 10
    result = evaluate(
        function_agent(agent),
        cases,
        old,
        new,
        score_mode(lambda case, output: output),
        samples=1,
        timeout_s=1,
        max_calls=40,
        min_pairs=20,
        cache=cache,
    )
    assert result.verdict.decision == "worse"
    assert result.verdict.mean == -1
    assert result.replay.calls == 40
    assert seen == ["0"] * 20 + ["0", "1"] * 20
    cached = replay(function_agent(agent), cases, old, new, samples=1, cache=cache, max_calls=0)
    assert cached.calls == 0 and not cached.budget_exhausted
    assert len(seen) == 60


def test_changed_case_input_invalidates_cache_under_same_id(tmp_path):
    agent = function_agent(lambda case, prompts: case["messages"])
    cache = Cache(tmp_path)
    run_prompts(agent, [Case("same", {"messages": ["old"]})], PROMPTS_A, cache=cache)
    result = run_prompts(
        agent,
        [Case("same", {"messages": ["new"]})],
        PROMPTS_A,
        cache=cache,
    )
    assert result.calls == 2
    assert all(run.output == ["new"] for run in result.runs)


def test_half_cached_pair_stops_before_exceeding_budget(tmp_path):
    agent = function_agent(RecordingAgent())
    cache = Cache(tmp_path)
    run_prompts(agent, make_cases(1), PROMPTS_A, samples=1, cache=cache)
    result = replay(agent, make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, max_calls=1, cache=cache)
    assert result.budget_exhausted
    assert result.calls == 0 and result.runs == []


def test_failed_refresh_cannot_resurrect_stale_cached_side(tmp_path):
    failing = [False]

    def agent(case, prompts):
        if failing[0] and prompts == PROMPTS_A:
            raise ValueError("A unavailable")
        return 1

    adapted = function_agent(agent)
    cache = Cache(tmp_path)
    run_prompts(adapted, make_cases(1), PROMPTS_A, samples=1, cache=cache)
    failing[0] = True
    for _ in range(2):
        result = replay(adapted, make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)
        assert result.calls == 2
        assert "A unavailable" in result.runs[0].error


def test_interrupted_pair_refresh_cannot_reuse_old_partner(tmp_path):
    class InterruptedCache(Cache):
        interrupted = False

        def put(self, *args, **kwargs):
            super().put(*args, **kwargs)
            if self.interrupted:
                raise OSError("interrupted after cache write")

    cache = InterruptedCache(tmp_path)
    agent = function_agent(RecordingAgent())
    run_prompts(agent, make_cases(1), PROMPTS_B, samples=1, cache=cache)
    cache.interrupted = True
    with pytest.raises(OSError, match="interrupted"):
        replay(agent, make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)
    cache.interrupted = False
    result = replay(agent, make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)
    assert result.calls == 2


def make_cases(n: int) -> list[Case]:
    return [Case(id=f"c{i}", input={"question": f"q{i}"}) for i in range(n)]


class RecordingAgent:
    """A real agent function that remembers every call it receives."""

    def __init__(self):
        self.calls = []

    def __call__(self, case_input, prompts):
        self.calls.append((case_input["question"], prompts["system"]))
        return {"answer": f"{case_input['question']} / {prompts['system']}"}


def test_runs_old_and_new_prompt_back_to_back_for_each_case_and_sample():
    agent = RecordingAgent()

    result = replay(function_agent(agent), make_cases(2), PROMPTS_A, PROMPTS_B, samples=2)

    assert agent.calls == [
        ("q0", "Answer briefly."),
        ("q0", "Answer briefly and cite the policy."),
        ("q0", "Answer briefly."),
        ("q0", "Answer briefly and cite the policy."),
        ("q1", "Answer briefly."),
        ("q1", "Answer briefly and cite the policy."),
        ("q1", "Answer briefly."),
        ("q1", "Answer briefly and cite the policy."),
    ]
    assert [(r.case_id, r.prompt_set, r.sample) for r in result.runs[:2]] == [
        ("c0", "A", 0),
        ("c0", "B", 0),
    ]
    assert result.calls == 8
    assert not result.budget_exhausted


def test_second_replay_with_the_same_cache_makes_no_calls(tmp_path):
    cache = Cache(tmp_path / "cache")
    first = replay(
        function_agent(RecordingAgent()), make_cases(3), PROMPTS_A, PROMPTS_B, cache=cache
    )

    agent = RecordingAgent()
    second = replay(function_agent(agent), make_cases(3), PROMPTS_A, PROMPTS_B, cache=cache)

    assert agent.calls == []
    assert second.calls == 0
    assert [r.output for r in second.runs] == [r.output for r in first.runs]
    assert all(r.cached for r in second.runs)


def test_cache_entries_expire_after_ttl(tmp_path):
    now = [1_000_000.0]
    cache = Cache(tmp_path / "cache", ttl_hours=24, clock=lambda: now[0])
    replay(function_agent(RecordingAgent()), make_cases(1), PROMPTS_A, PROMPTS_B, cache=cache)

    now[0] += 25 * 3600
    agent = RecordingAgent()
    result = replay(function_agent(agent), make_cases(1), PROMPTS_A, PROMPTS_B, cache=cache)

    assert result.calls == 4
    assert len(agent.calls) == 4


def test_cache_is_separate_per_agent_version(tmp_path):
    replay(
        function_agent(RecordingAgent()),
        make_cases(1),
        PROMPTS_A,
        PROMPTS_B,
        cache=Cache(tmp_path, agent_version="v1"),
    )

    result = replay(
        function_agent(RecordingAgent()),
        make_cases(1),
        PROMPTS_A,
        PROMPTS_B,
        cache=Cache(tmp_path, agent_version="v2"),
    )

    assert result.calls == 4


def test_agent_errors_are_recorded_not_cached_and_do_not_stop_the_run(tmp_path):
    def flaky(case_input, prompts):
        if case_input["question"] == "q1":
            raise ValueError("model refused")
        return "ok"

    cache = Cache(tmp_path)
    result = replay(
        function_agent(flaky), make_cases(3), PROMPTS_A, PROMPTS_B, samples=1, cache=cache
    )

    failed = [r for r in result.runs if r.error]
    assert {(r.case_id, r.prompt_set) for r in failed} == {("c1", "A"), ("c1", "B")}
    assert all("model refused" in r.error and r.output is None for r in failed)
    assert len(result.runs) == 6

    retry = replay(
        function_agent(flaky), make_cases(3), PROMPTS_A, PROMPTS_B, samples=1, cache=cache
    )
    assert retry.calls == 2  # only the failed pair runs again


def test_slow_agent_becomes_a_timed_out_run():
    def slow(case_input, prompts):
        time.sleep(1)
        return "late"

    result = replay(
        function_agent(slow), make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, timeout_s=0.05
    )

    assert all("timed out" in r.error for r in result.runs)


def test_call_budget_stops_before_a_pair_would_be_split():
    agent = RecordingAgent()

    result = replay(
        function_agent(agent), make_cases(5), PROMPTS_A, PROMPTS_B, samples=1, max_calls=5
    )

    assert result.budget_exhausted
    assert result.calls == 4
    keys = [(r.case_id, r.sample) for r in result.runs]
    assert all(keys.count(k) == 2 for k in keys)  # every pair is complete


def test_prompt_hash_ignores_key_order_and_changes_with_text():
    assert prompt_hash({"a": "x", "b": "y"}) == prompt_hash({"b": "y", "a": "x"})
    assert prompt_hash({"a": "x"}) != prompt_hash({"a": "x "})


def test_damaged_cache_entries_count_as_misses(tmp_path):
    cache = Cache(tmp_path)
    replay(
        function_agent(RecordingAgent()),
        make_cases(1),
        PROMPTS_A,
        PROMPTS_B,
        samples=1,
        cache=cache,
    )
    for path in tmp_path.glob("*.json"):
        path.write_text('{"unexpected": true}', encoding="utf-8")

    result = replay(
        function_agent(RecordingAgent()),
        make_cases(1),
        PROMPTS_A,
        PROMPTS_B,
        samples=1,
        cache=cache,
    )

    assert result.calls == 2
