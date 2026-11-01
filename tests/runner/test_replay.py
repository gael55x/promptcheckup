import time

from promptcheckup.data import Case
from promptcheckup.runner import Cache, prompt_hash, replay

PROMPTS_A = {"system": "Answer briefly."}
PROMPTS_B = {"system": "Answer briefly and cite the policy."}


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

    result = replay(agent, make_cases(2), PROMPTS_A, PROMPTS_B, samples=2)

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
    first = replay(RecordingAgent(), make_cases(3), PROMPTS_A, PROMPTS_B, cache=cache)

    agent = RecordingAgent()
    second = replay(agent, make_cases(3), PROMPTS_A, PROMPTS_B, cache=cache)

    assert agent.calls == []
    assert second.calls == 0
    assert [r.output for r in second.runs] == [r.output for r in first.runs]
    assert all(r.cached for r in second.runs)


def test_cache_entries_expire_after_ttl(tmp_path):
    now = [1_000_000.0]
    cache = Cache(tmp_path / "cache", ttl_hours=24, clock=lambda: now[0])
    replay(RecordingAgent(), make_cases(1), PROMPTS_A, PROMPTS_B, cache=cache)

    now[0] += 25 * 3600
    agent = RecordingAgent()
    result = replay(agent, make_cases(1), PROMPTS_A, PROMPTS_B, cache=cache)

    assert result.calls == 4
    assert len(agent.calls) == 4


def test_cache_is_separate_per_agent_version(tmp_path):
    replay(
        RecordingAgent(),
        make_cases(1),
        PROMPTS_A,
        PROMPTS_B,
        cache=Cache(tmp_path, agent_version="v1"),
    )

    result = replay(
        RecordingAgent(),
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
    result = replay(flaky, make_cases(3), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)

    failed = [r for r in result.runs if r.error]
    assert {(r.case_id, r.prompt_set) for r in failed} == {("c1", "A"), ("c1", "B")}
    assert all("model refused" in r.error and r.output is None for r in failed)
    assert len(result.runs) == 6

    retry = replay(flaky, make_cases(3), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)
    assert retry.calls == 2  # only the failed pair runs again


def test_slow_agent_becomes_a_timed_out_run():
    def slow(case_input, prompts):
        time.sleep(1)
        return "late"

    result = replay(slow, make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, timeout_s=0.05)

    assert all("timed out" in r.error for r in result.runs)


def test_call_budget_stops_before_a_pair_would_be_split():
    agent = RecordingAgent()

    result = replay(agent, make_cases(5), PROMPTS_A, PROMPTS_B, samples=1, max_calls=5)

    assert result.budget_exhausted
    assert result.calls == 4
    keys = [(r.case_id, r.sample) for r in result.runs]
    assert all(keys.count(k) == 2 for k in keys)  # every pair is complete


def test_prompt_hash_ignores_key_order_and_changes_with_text():
    assert prompt_hash({"a": "x", "b": "y"}) == prompt_hash({"b": "y", "a": "x"})
    assert prompt_hash({"a": "x"}) != prompt_hash({"a": "x "})


def test_damaged_cache_entries_count_as_misses(tmp_path):
    cache = Cache(tmp_path)
    replay(RecordingAgent(), make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)
    for path in tmp_path.glob("*.json"):
        path.write_text('{"unexpected": true}', encoding="utf-8")

    result = replay(RecordingAgent(), make_cases(1), PROMPTS_A, PROMPTS_B, samples=1, cache=cache)

    assert result.calls == 2
