import json
from dataclasses import replace

import pytest

from promptcheckup.checkup.checkup import checkup, propose_only
from promptcheckup.checkup.snapshot import load_snapshot
from promptcheckup.data import Case, load_cases
from promptcheckup.llm import LLMError
from promptcheckup.runner import AgentError, Cache, function_agent, prompt_hash

PROMPTS = {"system": "0\n" + "rule\n" * 9}


def cases(n=5):
    return [Case(f"c{i}", {"index": i}) for i in range(n)]


def agent(case, prompts):
    return case["index"] + int(prompts["system"].splitlines()[0])


def score(case, output):
    return output - case["index"]


def block(prompt, name):
    return json.loads(prompt.split(f"<{name}>\n")[1].split(f"\n</{name}>")[0])


def comparison(prompt):
    a, b = block(prompt, "response_1"), block(prompt, "response_2")
    return {"winner": "tie" if a == b else "1" if a > b else "2", "reason": "Higher wins."}


def llm_stub(*, edit="2", seen=None, rejected=False, pass_grades=False):
    def patterns(prompt):
        failures = block(prompt, "failures")
        return {
            "patterns": [
                {
                    "name": "Low",
                    "description": "Needs improvement",
                    "case_ids": [row[0] for row in failures],
                }
            ]
        }

    def proposal(prompt):
        examples = block(prompt, "examples")
        return {
            "prompts": {"system": edit + "\n" + "rule\n" * 9},
            "rationale": "Improve low scores.",
            "cited_case_ids": [] if rejected else [examples[0][0]],
        }

    replies = {
        "example_input": lambda prompt: {"cases": [{"index": i} for i in range(20)]},
        "failures": patterns,
        "examples": proposal,
        "agent_output": lambda prompt: {"pass": pass_grades, "reason": "below criteria"},
        "response_1": comparison,
    }

    def llm(prompt):
        if seen is not None:
            seen.append(prompt)
        kind = next(key for key in replies if f"<{key}>\n" in prompt)
        return json.dumps(replies[kind](prompt))

    return llm


def run(tmp_path, **settings):
    options = {"directory": tmp_path, "score_fn": score, "samples": 2, "min_pairs": 5}
    options.update(settings)
    return checkup(function_agent(agent), cases(), PROMPTS, llm_stub(rejected=True), **options)


def test_checkup_saves_first_snapshot_then_detects_only_actual_drift(tmp_path):
    state = [0]

    def changing(case, prompts):
        return agent(case, prompts) + state[0]

    def execute(version):
        return checkup(
            function_agent(changing),
            cases(),
            PROMPTS,
            llm_stub(rejected=True),
            directory=tmp_path,
            score_fn=score,
            min_pairs=5,
            agent_version=version,
        )

    first = execute("v1")
    assert first.drift is None
    assert load_snapshot(tmp_path, prompt_hash(PROMPTS)).agent_version == "v1"
    second = execute("v1")
    assert second.drift.verdict.decision == "inconclusive"
    assert not second.agent_version_changed
    state[0] = -2
    third = execute("v2")
    assert third.drift.verdict.decision == "worse"
    assert third.agent_version_changed
    assert load_snapshot(tmp_path, prompt_hash(PROMPTS)).runs[0].output == -2


def test_checkup_starter_creates_persisted_synthetic_cases(tmp_path):
    seen = []
    result = checkup(
        function_agent(agent),
        [],
        PROMPTS,
        llm_stub(rejected=True, seen=seen),
        directory=tmp_path,
        score_fn=score,
        description="Returns a number",
        example_input={"index": 0},
        min_pairs=5,
    )
    persisted = load_cases(["cases/starter.jsonl"], tmp_path)
    assert result.starter_created == len(persisted) == 20
    assert all(case.source == "synthetic" for case in persisted)
    assert result.synthetic_share == 1
    assert result.calls == 43  # starter + 40 agent runs + patterns + rejected proposal


@pytest.mark.parametrize("edit,kept,decision", [("2", True, "better"), ("-2", False, "worse")])
def test_checkup_keeps_only_proposals_that_pass_the_gate(tmp_path, edit, kept, decision):
    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(edit=edit),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
    )
    assert (result.proposal is not None) is kept
    assert result.proposal_verdict.decision == decision
    assert result.calls == 32  # 10 current, 2 LLM, 20 gate; no cache
    assert not (tmp_path / "system").exists()  # only the CLI writes prompt files


def test_checkup_records_rejection_without_gating(tmp_path):
    result = run(tmp_path)
    assert result.proposal is result.proposal_verdict is None
    assert "at least one" in result.proposal_rejection
    assert result.calls == 12


def test_checkup_judge_grades_sample_zero_and_feeds_patterns(tmp_path):
    seen = []
    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(seen=seen),
        directory=tmp_path,
        criteria="Higher is better.",
        min_pairs=5,
    )
    graded = [prompt for prompt in seen if "<agent_output>" in prompt]
    assert len(graded) == 5
    pattern_prompt = next(prompt for prompt in seen if "<failures>" in prompt)
    assert all(row[3] == "below criteria" for row in block(pattern_prompt, "failures"))
    assert result.proposal_verdict.decision == "better"
    assert result.proposal is not None
    assert result.calls == 67  # current 10, grades 5, patterns/proposal 2, gate 20+30


def test_judge_no_failures_makes_no_pattern_or_proposal_call(tmp_path):
    seen = []
    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(seen=seen, pass_grades=True),
        directory=tmp_path,
        min_pairs=5,
    )
    assert result.patterns == [] and result.proposal is None
    assert len(seen) == 5 and result.calls == 15


@pytest.mark.parametrize(
    "mode,cap",
    [
        ("score", 0),
        ("score", 9),
        ("score", 10),
        ("score", 11),
        ("score", 12),
        ("score", 31),
        ("judge", 14),
        ("judge", 16),
        ("judge", 66),
    ],
)
def test_every_step_shares_budget_and_never_keeps_partial_proposal(tmp_path, mode, cap):
    seen, agent_calls = [], []

    def counted(case, prompts):
        agent_calls.append(case)
        return agent(case, prompts)

    result = checkup(
        function_agent(counted),
        cases(),
        PROMPTS,
        llm_stub(seen=seen),
        directory=tmp_path,
        score_fn=score if mode == "score" else None,
        max_calls=cap,
        min_pairs=5,
    )
    assert result.budget_exhausted
    assert result.calls == len(seen) + len(agent_calls) <= cap
    assert result.proposal is None
    if cap < 10:
        assert load_snapshot(tmp_path, prompt_hash(PROMPTS)) is None


def test_starter_respects_zero_budget_and_writes_nothing(tmp_path):
    result = checkup(
        function_agent(agent),
        [],
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=score,
        max_calls=0,
    )
    assert result.budget_exhausted and result.calls == 0
    assert not (tmp_path / "cases").exists()


def test_propose_only_has_no_starter_or_snapshot(tmp_path):
    result = propose_only(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
    )
    assert result.proposal is not None
    assert result.drift is None and result.starter_created == 0
    assert not (tmp_path / ".promptcheckup/snapshots").exists()
    with pytest.raises(ValueError, match="cases"):
        propose_only(function_agent(agent), [], PROMPTS, llm_stub(), directory=tmp_path)


def test_score_failures_use_mean_over_samples_and_ceil_twenty_percent(tmp_path):
    seen, counts = [], {}

    def varying(case, prompts):
        index = case["index"]
        counts[index] = counts.get(index, 0) + 1
        return -10 if index == 5 and counts[index] % 2 == 0 else index

    checkup(
        function_agent(varying),
        cases(6),
        PROMPTS,
        llm_stub(rejected=True, seen=seen),
        directory=tmp_path,
        score_fn=lambda case, output: output,
        min_pairs=5,
    )
    failures = block(next(prompt for prompt in seen if "<failures>" in prompt), "failures")
    assert [row[0] for row in failures] == ["c5", "c0"]
    assert failures[0][3] == "low score -2.5"


def test_agent_errors_are_failures_and_remain_reportable(tmp_path):
    def broken(case, prompts):
        raise ValueError("agent unavailable")

    result = checkup(
        function_agent(broken),
        cases(),
        PROMPTS,
        llm_stub(rejected=True),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
    )
    assert len(result.errors) == 10
    assert result.patterns[0].case_ids == [case.id for case in cases()]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "not a number"])
def test_invalid_failure_scores_are_loud(tmp_path, value):
    with pytest.raises(AgentError, match="scoring.score"):
        run(tmp_path, score_fn=lambda case, output: value)


def test_invalid_llm_reply_is_not_a_rejected_or_partial_proposal(tmp_path):
    with pytest.raises(LLMError):
        checkup(
            function_agent(agent),
            cases(),
            PROMPTS,
            lambda prompt: "{}",
            directory=tmp_path,
            score_fn=score,
            min_pairs=5,
        )


def test_drift_uses_only_cases_shared_with_previous_snapshot(tmp_path):
    run(tmp_path)
    result = checkup(
        function_agent(agent),
        cases(6)[1:],
        PROMPTS,
        llm_stub(rejected=True),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
    )
    assert result.drift.verdict.n == 4
    assert result.drift.verdict.decision == "inconclusive"


def test_cache_hits_are_free_across_current_and_gate_runs(tmp_path):
    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
        cache=Cache(tmp_path / "cache"),
    )
    assert result.calls == 22  # 10 current + 2 LLM + 10 new-prompt runs


def test_redaction_precedes_starter_storage_snapshots_cache_and_llm(tmp_path):
    seen = []

    def redact(value):
        return json.loads(json.dumps(value).replace("private-value", "redacted"))

    def private_agent(case, prompts):
        return {"value": agent(case, prompts), "private": "private-value"}

    originals = [
        replace(case, input={**case.input, "private": "private-value"}) for case in cases()
    ]
    result = checkup(
        function_agent(private_agent),
        originals,
        PROMPTS,
        llm_stub(seen=seen, rejected=True),
        directory=tmp_path,
        score_fn=lambda case, output: output["value"],
        min_pairs=5,
        redact=redact,
        cache=Cache(tmp_path / "cache"),
    )
    assert "private-value" not in "".join(seen)
    assert all("private-value" not in path.read_text() for path in tmp_path.rglob("*.json"))
    assert result.calls == 12


def test_redaction_never_changes_inputs_received_by_agent_during_gate(tmp_path):
    received = []

    def private_agent(case, prompts):
        received.append(case["private"])
        return agent(case, prompts)

    def redact(value):
        if isinstance(value, dict):
            return {key: text for key, text in value.items() if key != "private"}
        return value

    originals = [replace(case, input={**case.input, "private": "original"}) for case in cases()]
    result = checkup(
        function_agent(private_agent),
        originals,
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
        redact=redact,
    )
    assert result.proposal is not None
    assert received == ["original"] * 30


def test_judge_still_grades_successful_sample_zero_if_another_sample_errors(tmp_path):
    seen, counts = [], {}

    def sometimes_broken(case, prompts):
        key = case["index"]
        counts[key] = counts.get(key, 0) + 1
        if counts[key] % 2 == 0:
            raise ValueError("second sample failed")
        return agent(case, prompts)

    result = checkup(
        function_agent(sometimes_broken),
        cases(),
        PROMPTS,
        llm_stub(rejected=True, seen=seen),
        directory=tmp_path,
        min_pairs=5,
    )
    assert len([prompt for prompt in seen if "<agent_output>" in prompt]) == 5
    assert len(result.errors) == 5


def test_drift_judging_shares_budget_and_preserves_previous_snapshot_when_incomplete(tmp_path):
    first = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(pass_grades=True),
        directory=tmp_path,
        min_pairs=5,
        agent_version="v1",
    )
    previous = load_snapshot(tmp_path, prompt_hash(PROMPTS))
    seen = []
    second = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(seen=seen),
        directory=tmp_path,
        min_pairs=5,
        max_calls=39,
        agent_version="v2",
    )
    assert first.calls == 15
    assert second.budget_exhausted
    assert second.calls == 10 + len(seen) <= 39
    assert second.drift.verdict.decision == "inconclusive"
    assert "budget" in second.drift.verdict.reason
    assert load_snapshot(tmp_path, prompt_hash(PROMPTS)) == previous


def test_configured_scorer_receives_budget_and_min_pairs_without_replay_settings(tmp_path):
    from promptcheckup.checkup.evaluate import score_mode

    observed = []
    actual = score_mode(score)

    def scorer(cases, replayed, *, budget, min_pairs):
        observed.append((budget, min_pairs))
        return actual(cases, replayed, budget=budget, min_pairs=min_pairs)

    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=score,
        scorer=scorer,
        min_pairs=5,
        max_calls=32,
    )
    assert result.proposal is not None and result.calls == 32
    assert observed == [(0, 5)]


def test_repeat_checkups_report_each_failed_run_once(tmp_path):
    def broken(case, prompts):
        raise ValueError("agent unavailable")

    def execute():
        return checkup(
            function_agent(broken),
            cases(),
            PROMPTS,
            llm_stub(rejected=True),
            directory=tmp_path,
            score_fn=score,
            min_pairs=5,
        )

    first, second = execute(), execute()

    assert len(first.errors) == len(second.errors) == 10
    assert second.drift is not None
