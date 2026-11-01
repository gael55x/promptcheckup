from dataclasses import replace

from promptcheckup.checkup.checkup import CheckupResult
from promptcheckup.checkup.report import checkup_report
from promptcheckup.llm.patterns import Pattern
from promptcheckup.llm.propose import Proposal
from promptcheckup.stats import decide
from tests.checkup.test_report import evaluation


def result(**changes):
    return replace(
        CheckupResult(0, None, False, [], None, None, None, 0, snapshot_saved=True), **changes
    )


def test_stable_checkup_headline_and_first_snapshot():
    text = checkup_report(result(), version="1.2.3")
    assert text.startswith("Stable. No drift and no new failure patterns.\n")
    assert "first check-up: snapshot saved" in text
    assert "Calls: 0" in text and "1.2.3" in text


def test_drift_headline_takes_priority_and_reports_version_change():
    text = checkup_report(
        result(drift=evaluation([-2] * 5), agent_version_changed=True), version="test"
    )
    assert text.startswith("Your agent got worse this week without a prompt change")
    assert "(agent version changed)" in text.splitlines()[0]
    assert "5 of 5 cases regressed" in text


def test_passed_proposal_headline_summary_and_citations():
    text = checkup_report(
        result(
            proposal=Proposal({"system": "new"}, "Fix a problem.", ["c1"]),
            proposal_verdict=decide([1] * 5, min_pairs=5),
        ),
        version="test",
    )
    assert text.splitlines()[0] == (
        "A prompt change passed the gate: 5 of 5 cases improved (95% CI +1.00 to +1.00). "
        "Review the proposed edit before merging."
    )
    assert "Fix a problem." in text and "c1" in text
    assert "| 5 | +1.00 | +1.00 to +1.00 | 5 | 0 | 0 |" in text  # the gate's evidence


def test_patterns_and_synthetic_share_are_explicit():
    text = checkup_report(
        result(patterns=[Pattern("Wrong", "Incorrect", ["c1", "c2"])], synthetic_share=0.4),
        version="test",
    )
    assert "1 failure pattern" in text.splitlines()[0]
    assert "c1" in text and "c2" in text
    assert "Synthetic cases: 40%" in text
    assert "recorded" in text


def test_rejected_and_failed_gate_reasons_are_reported():
    rejected = checkup_report(result(proposal_rejection="too many lines"), version="test")
    failed = checkup_report(result(proposal_verdict=decide([0] * 5, min_pairs=5)), version="test")
    assert "too many lines" in rejected
    assert "did not pass the gate" in failed


def test_budget_report_cannot_claim_stability():
    text = checkup_report(result(budget_exhausted=True), version="test")
    assert "budget" in text.splitlines()[0]
    assert not text.startswith("Stable")


def test_agent_version_change_is_reported_even_without_drift():
    text = checkup_report(
        result(drift=evaluation([0] * 5), agent_version_changed=True), version="test"
    )
    assert "Agent version changed: yes." in text
