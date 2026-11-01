import pytest

from promptcheckup.checkup.checkup import checkup, propose_only
from promptcheckup.checkup.evolve import split_cases
from promptcheckup.checkup.report import checkup_report, proposal_report
from promptcheckup.runner import function_agent
from tests.checkup.test_checkup import PROMPTS, agent, cases, llm_stub, score


@pytest.mark.parametrize("runner", [checkup, propose_only])
def test_library_holdout_keeps_learning_requests_disjoint(tmp_path, runner):
    data = cases(40)
    seen = []
    result = runner(
        function_agent(agent),
        data,
        PROMPTS,
        llm_stub(seen=seen),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
        holdout_share=0.5,
    )
    learning, held = split_cases(data, 0.5)
    assert result.proposal is not None
    assert result.proposal_verdict.n == len(held)
    requests = "\n".join(seen)
    assert all(f'"{case.id}"' not in requests for case in held)
    assert set(result.proposal.cited_case_ids) <= {case.id for case in learning}


def test_holdout_keeps_all_cases_in_drift_and_failure_report(tmp_path):
    data = cases(40)
    settings = dict(directory=tmp_path, score_fn=score, min_pairs=5, holdout_share=0.5)
    checkup(function_agent(agent), data, PROMPTS, llm_stub(rejected=True), **settings)
    result = checkup(
        function_agent(agent), data, {"system": "-2"}, llm_stub(rejected=True), **settings
    )
    assert result.drift.verdict.n == len(data)
    assert result.drift.verdict.decision == "worse"
    # Tied scores select the first 20%, regardless of the learning/holdout split.
    assert [failure[0] for failure in result.failures] == [case.id for case in data[:8]]
    report = checkup_report(result, version="test")
    assert all(f"| {case.id} |" in report for case in data[:8])


def test_small_holdout_retains_reviewable_edit_without_gating(tmp_path):
    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=score,
        min_pairs=5,
        holdout_share=0.5,
    )
    _, held = split_cases(cases(), 0.5)
    assert result.proposal is result.proposal_verdict is result.gated_proposal is None
    assert result.calls == 12
    assert result.proposal_rejection == (
        f"not tested: {len(held)} held-out cases, need at least 5; add cases"
    )
    report = proposal_report(result, originals=PROMPTS, version="test")
    assert "Improve low scores." in report and "Proposed prompt edits:" in report


@pytest.mark.parametrize("share", [0, 1, True, float("nan"), "0.5"])
def test_library_rejects_invalid_holdout_before_calls(tmp_path, share):
    seen = []
    with pytest.raises(ValueError, match="holdout_share must be between 0 and 1"):
        checkup(
            function_agent(agent),
            cases(),
            PROMPTS,
            llm_stub(seen=seen),
            directory=tmp_path,
            score_fn=score,
            holdout_share=share,
        )
    assert seen == []
