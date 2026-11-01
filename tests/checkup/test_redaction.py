import json
from dataclasses import asdict

import pytest

from promptcheckup.checkup.checkup import checkup
from promptcheckup.checkup.report import checkup_report
from promptcheckup.runner import Cache, function_agent
from tests.checkup.test_checkup import PROMPTS, cases, llm_stub

SECRET = "private-value"


def redact(value):
    return json.loads(json.dumps(value).replace(SECRET, "[redacted]"))


def test_agent_error_text_is_redacted_before_snapshots_reports_and_llm(tmp_path):
    def agent(case, prompts):
        if case["index"] == 0:
            raise ValueError(SECRET)
        return "safe"

    seen = []
    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(seen=seen, rejected=True),
        directory=tmp_path,
        score_fn=lambda case, output: 0,
        min_pairs=5,
        redact=redact,
        cache=Cache(tmp_path / "cache"),
    )
    assert SECRET not in json.dumps(asdict(result))
    assert SECRET not in checkup_report(result, version="test")
    assert SECRET not in "\n".join(seen)
    assert all(SECRET not in path.read_text() for path in tmp_path.rglob("*.json"))
    assert "[redacted]" in result.errors[0].error


@pytest.mark.parametrize("failure", ["raise", "not-string"])
def test_error_redaction_failure_withholds_exception_text(tmp_path, failure):
    def agent(case, prompts):
        raise ValueError(SECRET)

    def broken_redactor(value):
        if failure == "raise":
            raise ValueError(SECRET)
        return {"unsafe": SECRET}

    result = checkup(
        function_agent(agent),
        cases(),
        PROMPTS,
        llm_stub(),
        directory=tmp_path,
        score_fn=lambda case, output: 0,
        min_pairs=5,
        redact=broken_redactor,
    )
    assert all(run.error == "error text withheld: redaction failed" for run in result.errors)
    assert SECRET not in checkup_report(result, version="test")


def test_old_snapshot_outputs_and_errors_are_redacted_before_drift(tmp_path):
    def old_agent(case, prompts):
        if case["index"] == 0:
            raise ValueError(SECRET)
        return "answer " + SECRET

    checkup(
        function_agent(old_agent),
        cases(20),
        PROMPTS,
        llm_stub(rejected=True),
        directory=tmp_path,
        score_fn=lambda case, output: 0,
    )
    assert any(SECRET in path.read_text() for path in tmp_path.rglob("*.json"))
    seen = []
    result = checkup(
        function_agent(lambda case, prompts: "answer " + SECRET),
        cases(20),
        PROMPTS,
        llm_stub(seen=seen, pass_grades=True),
        directory=tmp_path,
        min_pairs=5,
        redact=redact,
    )
    assert result.drift is not None
    assert SECRET not in "\n".join(seen)
    assert SECRET not in json.dumps(asdict(result))
    assert SECRET not in checkup_report(result, version="test")
    assert all(SECRET not in path.read_text() for path in tmp_path.rglob("*.json"))
