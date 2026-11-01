import json
import shlex
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import pytest

from promptcheckup.cli.config import load_config
from promptcheckup.cli.doctor import PROBE
from promptcheckup.cli.main import _parser, main
from promptcheckup.data import Case, append_cases
from promptcheckup.llm import LLMError

ORDER = ["config", "prompts", "cases", "agent", "prompt-use", "score", "llm", "budget"]


@pytest.fixture
def project(tmp_path):
    module = "doctor_" + uuid4().hex
    (tmp_path / "module-name").write_text(module)
    write_agent(tmp_path, 'return {"answer": prompts["prompts/system.md"] + case["question"]}')
    (tmp_path / "prompts").mkdir()
    (tmp_path / "prompts/system.md").write_text("Say hello.\n")
    (tmp_path / "promptcheckup.toml").write_text(
        f'[agent]\nentry = "{module}:run"\nexample_input = {{question = "example"}}\n'
        'timeout_s = 0.1\n[prompts]\nfiles = ["prompts/system.md"]\n'
        '[cases]\npaths = ["cases/*.jsonl"]\n'
        f'[scoring]\nmode = "score"\nscore = "{module}:score"\n'
        "[run]\nmin_pairs = 5\nsamples = 2\nmax_calls = 400\n"
    )
    append_cases(
        tmp_path / "cases/data.jsonl",
        [
            Case(str(i), {"question": str(i)}, "synthetic" if i < 2 else "recorded")
            for i in range(5)
        ],
    )
    yield tmp_path
    sys.modules.pop(module, None)


def write_agent(project, body, score="return len(output['answer'])"):
    module = (project / "module-name").read_text()
    (project / f"{module}.py").write_text(
        "import json\nfrom pathlib import Path\nimport time\n"
        "def run(case, prompts):\n"
        "    with Path('calls.jsonl').open('a') as stream:\n"
        "        stream.write(json.dumps({'case': case, 'prompts': prompts}) + '\\n')\n"
        f"    {body}\n"
        f"def score(case, output):\n    {score}\n"
    )


def edit_config(project, old, new):
    path = project / "promptcheckup.toml"
    path.write_text(path.read_text().replace(old, new))


def add_llm(project, *, judge=True, key=""):
    path = project / "promptcheckup.toml"
    text = path.read_text()
    if judge:
        text = text.replace('mode = "score"', 'mode = "judge"\ncriteria = "Answer the question."')
    path.write_text(
        text + '\n[llm]\nbase_url = "https://example.invalid/v1"\n'
        f'model = "test-judge"\napi_key_env = "{key}"\n'
    )


def execute(project, *options, llm=None):
    from promptcheckup.cli.doctor import doctor

    args = _parser().parse_args(
        ["doctor", "--config", str(project / "promptcheckup.toml"), *options]
    )
    return doctor(args, llm=llm)


def rows(capsys):
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("doctor makes 2 agent calls and ")
    parsed = [line.split(maxsplit=2) for line in lines[1:]]
    assert [name for _, name, _ in parsed] == ORDER
    return {name: (status, message) for status, name, message in parsed}


def test_doctor_working_score_project_has_ordered_checks(project, capsys):
    before = Path.cwd()
    assert execute(project) == 0
    result = rows(capsys)
    assert all(result[name][0] == "ok" for name in ORDER if name not in ("llm", "cases"))
    assert result["cases"][0] == "warn"  # five total cases cannot supply five held-out cases
    assert result["llm"][0] == "skip"
    assert "prompts/system.md" in result["prompts"][1]
    assert "5 cases" in result["cases"][1] and "40% synthetic" in result["cases"][1]
    assert "ran in" in result["agent"][1] and '"answer"' in result["agent"][1]
    assert Path.cwd() == before
    assert not (project / ".promptcheckup").exists()


def test_doctor_check_is_frozen():
    from promptcheckup.cli.doctor import Check

    result = Check("config", "ok", "ready")
    with pytest.raises(FrozenInstanceError):
        result.status = "fail"


def test_doctor_warns_when_holdout_is_below_min_pairs(project, capsys):
    assert execute(project) == 0
    status, message = rows(capsys)["cases"]
    assert status == "warn"
    assert "held-out cases" in message and "need at least 5" in message


def test_doctor_redacts_agent_exception_text(project, capsys):
    write_agent(project, 'raise ValueError("private-value")')
    module = (project / "module-name").read_text()
    with (project / f"{module}.py").open("a") as stream:
        stream.write('def redact(value):\n    return str(value).replace("private-value", "safe")\n')
    edit_config(project, "max_calls = 400", f'max_calls = 400\nredact = "{module}:redact"')
    assert execute(project) == 3
    captured = capsys.readouterr()
    assert "private-value" not in captured.out + captured.err


def test_checkup_plan_keeps_all_case_drift_cost_with_smaller_holdout(project):
    from promptcheckup.cli.checkup import planned_checkup_calls

    add_llm(project)
    config = load_config(project / "promptcheckup.toml")
    plan = planned_checkup_calls(config, 20, "checkup", n_gate=5)
    assert plan == {"agent": 60, "llm": 30 + 120 + 20 + 2}


def test_doctor_bad_config_skips_every_dependent_check(project, capsys):
    (project / "promptcheckup.toml").write_text("[broken\n")
    assert main(["doctor", "--config", str(project / "promptcheckup.toml")]) == 3
    captured = capsys.readouterr()
    assert "invalid config" in captured.out
    assert sum(line.startswith("skip  ") for line in captured.out.splitlines()) == 7
    assert len(captured.err.splitlines()) == 1 and "Traceback" not in captured.err
    assert not (project / "calls.jsonl").exists()


@pytest.mark.parametrize(
    "body,reason",
    [
        ("raise RuntimeError('broken\\nagent')", "broken agent"),
        ("return object()", "not JSON-serializable"),
        ("time.sleep(0.2); return {}", "timed out"),
    ],
)
def test_doctor_agent_failures_skip_consumers(project, capsys, body, reason):
    write_agent(project, body)
    assert execute(project) == 3
    result = rows(capsys)
    assert result["agent"][0] == "fail" and reason in result["agent"][1]
    for name in ("prompt-use", "score", "llm"):
        assert result[name] == ("skip", "needs a working agent")
    assert result["budget"][0] == "ok"
    assert len((project / "calls.jsonl").read_text().splitlines()) == 1


def test_doctor_missing_agent_module(project, capsys):
    module = (project / "module-name").read_text()
    (project / f"{module}.py").unlink()
    assert execute(project) == 3
    result = rows(capsys)
    assert "could not import" in result["agent"][1] and module in result["agent"][1]
    assert result["score"][0] == "skip"


def command(project, script):
    module = (project / "module-name").read_text()
    argv = shlex.join([sys.executable, "-c", script])
    edit_config(project, f'entry = "{module}:run"', f"cmd = {json.dumps(argv)}")
    edit_config(project, "timeout_s = 0.1", "timeout_s = 1")


def test_doctor_real_command_agent(project, capsys):
    command(
        project,
        "import json, sys; from pathlib import Path; data = json.load(sys.stdin); "
        "Path('cmd-calls').open('a').write('call\\n'); "
        "print(json.dumps({'answer': data['prompts']['prompts/system.md']}))",
    )
    assert execute(project) == 0
    assert rows(capsys)["prompt-use"][0] == "ok"
    assert (project / "cmd-calls").read_text().splitlines() == ["call", "call"]


def test_doctor_missing_command(project, capsys):
    module = (project / "module-name").read_text()
    edit_config(project, f'entry = "{module}:run"', 'cmd = "promptcheckup-missing-executable"')
    assert execute(project) == 3
    result = rows(capsys)
    assert "promptcheckup-missing-executable" in result["agent"][1]
    assert result["prompt-use"][0] == "skip"


@pytest.mark.parametrize(
    "script,reason",
    [
        ("print('bad json')", "invalid JSON"),
        ("raise RuntimeError('command crash')", "command crash"),
    ],
)
def test_doctor_command_failures(project, capsys, script, reason):
    command(project, script)
    assert execute(project) == 3
    assert reason in rows(capsys)["agent"][1]


@pytest.mark.parametrize(
    "score", ["return 'not a number'", "return float('nan')", "return float('inf')"]
)
def test_doctor_requires_finite_score(project, capsys, score):
    write_agent(project, "return {'answer': prompts['prompts/system.md']}", score)
    assert execute(project) == 3
    result = rows(capsys)
    assert result["score"][0] == "fail" and "scoring.score" in result["score"][1]
    assert result["llm"][0] == "skip"


def test_doctor_ignoring_prompts_warns_with_adapter_hint(project, capsys):
    write_agent(project, "return {'answer': 'constant'}")
    assert execute(project) == 0
    assert rows(capsys)["prompt-use"] == (
        "warn",
        "the output did not change when the prompts changed. Does your agent use the `prompts` "
        "argument instead of reading prompt files itself? See docs/connect-your-agent.md",
    )


def test_doctor_detects_an_agent_that_only_reacts_to_part_of_its_prompt(project, capsys):
    # Uses its prompts correctly, but only a keyword matters: appending text would not
    # change the output, so the probe must replace the prompt text.
    write_agent(
        project,
        "return {'answer': 'formal' if 'hello' in prompts['prompts/system.md'] else 'casual'}",
    )
    assert execute(project) == 0
    assert rows(capsys)["prompt-use"] == ("ok", "the output changed with the supplied prompts")


def test_doctor_probe_failure_shows_the_agent_reads_its_prompts(project, capsys):
    write_agent(
        project, "assert 'probe' not in prompts['prompts/system.md']; return {'answer': 'ok'}"
    )
    assert execute(project) == 0  # real gates never send the probe text
    result = rows(capsys)
    assert result["prompt-use"][0] == "warn"
    assert "uses the supplied prompts" in result["prompt-use"][1]
    assert "AssertionError" in result["prompt-use"][1] or "failed" in result["prompt-use"][1]
    assert result["score"][0] == "ok"


def test_doctor_missing_llm_key_does_not_leak_environment(project, capsys, monkeypatch):
    monkeypatch.delenv("PROMPTCHECKUP_DOCTOR_MISSING", raising=False)
    monkeypatch.setenv("PROMPTCHECKUP_DOCTOR_OTHER", "fake-private-test-value")
    add_llm(project, key="PROMPTCHECKUP_DOCTOR_MISSING")

    def unused(prompt):
        pytest.fail("must not call the judge without its configured key")

    assert execute(project, llm=unused) == 3
    captured = capsys.readouterr()
    assert "set PROMPTCHECKUP_DOCTOR_MISSING in this shell" in captured.out
    assert "fake-private-test-value" not in captured.out + captured.err


@pytest.mark.parametrize("passed", [True, False])
def test_doctor_llm_connection_reports_model_and_grade(project, capsys, passed, monkeypatch):
    monkeypatch.setenv("PROMPTCHECKUP_DOCTOR_KEY", "fake-private-test-value")
    add_llm(project, key="PROMPTCHECKUP_DOCTOR_KEY")
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return "Thinking first.\n" + json.dumps({"pass": passed, "reason": "A test verdict."})

    assert execute(project, llm=llm) == 0
    captured = capsys.readouterr()
    assert "doctor makes 2 agent calls and 1 judge call" in captured.out.splitlines()[0]
    assert "test-judge" in captured.out and f"passed={passed}" in captured.out
    assert "PROMPTCHECKUP_DOCTOR_KEY is set" in captured.out
    assert "fake-private-test-value" not in captured.out + captured.err
    assert len(calls) == 1 and "Answer the question." in calls[0]


def test_doctor_llm_error_is_a_single_line_failure(project, capsys):
    add_llm(project)

    def llm(prompt):
        raise LLMError("provider\nfailed")

    assert execute(project, llm=llm) == 3
    status, message = rows(capsys)["llm"]
    assert status == "fail" and "provider failed" in message


def test_doctor_no_llm_skips_even_a_missing_key(project, capsys):
    add_llm(project, key="PROMPTCHECKUP_DOCTOR_MISSING")

    def unused(prompt):
        pytest.fail("--no-llm must never call the LLM")

    assert execute(project, "--no-llm", llm=unused) == 0
    assert rows(capsys)["llm"] == ("skip", "skipped by --no-llm")


def test_doctor_score_mode_with_llm_checks_it_once(project, capsys):
    add_llm(project, judge=False)
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '{"pass": true, "reason": "Connected."}'

    assert execute(project, llm=llm) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("doctor makes 2 agent calls and 1 judge call")
    assert len(calls) == 1


def test_doctor_no_cases_uses_example_input_and_starter_hint(project, capsys):
    (project / "cases/data.jsonl").unlink()
    assert execute(project) == 0
    result = rows(capsys)
    assert result["cases"][0] == "warn"
    assert (
        "no cases yet: `promptcheckup checkup` will generate 20 starter cases" in result["cases"][1]
    )
    assert "@record" in result["cases"][1]
    calls = [json.loads(line) for line in (project / "calls.jsonl").read_text().splitlines()]
    assert [call["case"] for call in calls] == [{"question": "example"}] * 2
    assert not (project / "cases/starter.jsonl").exists()


def test_doctor_fewer_cases_warns_about_min_pairs(project, capsys):
    edit_config(project, "min_pairs = 5", "min_pairs = 20")
    assert execute(project) == 0
    status, message = rows(capsys)["cases"]
    assert status == "warn" and "not sure yet" in message and "20" in message


def test_doctor_small_budget_reports_both_estimates(project, capsys):
    edit_config(project, "max_calls = 400", "max_calls = 1")
    assert execute(project) == 0
    status, message = rows(capsys)["budget"]
    assert status == "warn"
    assert "a full gate needs 20 calls but max_calls = 1" in message
    assert "a full check-up needs 32 calls but max_calls = 1" in message
    assert "the verdict would stop at not sure yet" in message


def test_doctor_two_calls_probe_every_prompt_without_writing_files(project, capsys):
    (project / "prompts/tools.md").write_text("Tools.\n")
    edit_config(project, '["prompts/system.md"]', '["prompts/system.md", "prompts/tools.md"]')
    assert execute(project) == 0
    rows(capsys)
    calls = [json.loads(line) for line in (project / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 2 and calls[0]["case"] == calls[1]["case"] == {"question": "0"}
    for name, text in calls[0]["prompts"].items():
        assert calls[1]["prompts"][name] == PROBE
        assert (project / name).read_text() == text


@pytest.mark.parametrize("broken", ["prompts", "cases"])
def test_doctor_unreadable_inputs_skip_agent(project, capsys, broken):
    paths = {"prompts": "prompts/system.md", "cases": "cases/data.jsonl"}
    (project / paths[broken]).write_bytes(b"\xff")
    assert execute(project) == 3
    result = rows(capsys)
    assert result[broken][0] == "fail" and result["agent"][0] == "skip"
    assert not (project / "calls.jsonl").exists()


def test_doctor_truncates_output_preview(project, capsys):
    write_agent(project, "return {'answer': 'x' * 500}")
    assert execute(project) == 0
    message = rows(capsys)["agent"][1]
    assert "x" * 121 not in message


def test_doctor_redacts_previews_and_judge_inputs(project, capsys):
    module = (project / "module-name").read_text()
    write_agent(
        project, "return {'answer': 'private-output', 'safe': prompts['prompts/system.md']}"
    )
    with (project / f"{module}.py").open("a") as stream:
        stream.write(
            "def redact(value):\n    return {k: v for k, v in value.items() if k == 'safe'}\n"
        )
    edit_config(project, "max_calls = 400", f'max_calls = 400\nredact = "{module}:redact"')
    add_llm(project)
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return '{"pass": true, "reason": "Connected."}'

    assert execute(project, llm=llm) == 0
    captured = capsys.readouterr()
    assert "private-output" not in captured.out + captured.err + calls[0]
    assert '"question"' not in calls[0]


def test_doctor_invalid_redactor_output_is_a_failure(project, capsys):
    module = (project / "module-name").read_text()
    with (project / f"{module}.py").open("a") as stream:
        stream.write("def redact(value):\n    return object()\n")
    edit_config(project, "max_calls = 400", f'max_calls = 400\nredact = "{module}:redact"')
    assert execute(project) == 3
    assert "run.redact" in rows(capsys)["agent"][1]


def test_doctor_preserves_input_and_prompts_when_agent_mutates_them(project, capsys):
    write_agent(project, "case.clear(); return {'answer': prompts.pop('prompts/system.md')}")
    assert execute(project) == 0
    result = rows(capsys)
    assert result["prompt-use"][0] == "ok"
    calls = [json.loads(line) for line in (project / "calls.jsonl").read_text().splitlines()]
    assert [call["case"] for call in calls] == [{"question": "0"}] * 2


def test_doctor_score_llm_table_requires_endpoint_and_model(project, capsys):
    add_llm(project, judge=False)
    edit_config(project, 'model = "test-judge"', 'model = ""')

    def unused(prompt):
        pytest.fail("must validate the LLM configuration before invoking it")

    assert execute(project, llm=unused) == 3
    assert "llm.model" in rows(capsys)["llm"][1]


@pytest.mark.parametrize(
    "mode,count,command_name,expected",
    [
        ("score", 5, "checkup", 32),
        ("judge", 5, "checkup", 97),
        ("judge", 5, "propose", 67),
        ("score", 0, "checkup", 123),
        ("judge", 0, "checkup", 383),
    ],
)
def test_planned_checkup_calls_preserves_estimate(project, mode, count, command_name, expected):
    from promptcheckup.cli.checkup import planned_checkup_calls

    if mode == "judge":
        add_llm(project)
    config = load_config(project / "promptcheckup.toml")
    assert sum(planned_checkup_calls(config, count, command_name).values()) == expected
