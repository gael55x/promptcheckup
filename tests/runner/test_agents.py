import sys
import time
from threading import Event

import pytest

from promptcheckup.runner import (
    AgentError,
    call_agent,
    command_agent,
    function_agent,
    python_agent,
)


@pytest.fixture
def module_path(tmp_path):
    sys.path.insert(0, str(tmp_path))
    try:
        yield tmp_path
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("promptcheckup_test_agent", None)


def test_python_agent_imports_and_calls_real_function(module_path):
    (module_path / "promptcheckup_test_agent.py").write_text(
        "def run(case, prompts):\n    return {'answer': prompts['system'] + case['question']}\n",
        encoding="utf-8",
    )

    agent = python_agent("promptcheckup_test_agent:run")

    assert call_agent(agent, {"question": "hello"}, {"system": "say "}, timeout_s=1) == {
        "answer": "say hello"
    }


@pytest.mark.parametrize("entry", ["", "missing_colon", ":run", "json:", "json:loads:extra"])
def test_python_agent_rejects_malformed_entry(entry):
    with pytest.raises(AgentError, match="module:function"):
        python_agent(entry)


@pytest.mark.parametrize(
    ("contents", "reason"),
    [
        (None, "import"),
        ("def broken(\n", "import"),
        ("raise RuntimeError('import failed')\n", "import"),
        ("other = 1\n", "run"),
        ("run = 42\n", "callable"),
    ],
)
def test_python_agent_reports_import_attribute_and_callable_errors(module_path, contents, reason):
    if contents is not None:
        (module_path / "promptcheckup_test_agent.py").write_text(contents, encoding="utf-8")

    with pytest.raises(AgentError, match=reason):
        python_agent("promptcheckup_test_agent:run")


@pytest.mark.parametrize("output", [None, False, 42, "answer", [1, 2], {"answer": "yes"}])
def test_call_agent_preserves_json_output_and_passes_arguments(output):
    case_input = {"question": "hello"}
    prompts = {"system": "be helpful"}

    def agent(case, received_prompts):
        assert case is case_input
        assert received_prompts is prompts
        return output

    assert call_agent(function_agent(agent), case_input, prompts, timeout_s=1) is output


@pytest.mark.parametrize("error_type", [ValueError, TimeoutError, AgentError])
def test_call_agent_chains_python_exception_without_misreporting_timeout(error_type):
    error = error_type("agent failed")

    def agent(case, prompts):
        raise error

    with pytest.raises(AgentError, match="agent failed") as caught:
        call_agent(function_agent(agent), {}, {}, timeout_s=1)

    assert caught.value is not error
    assert caught.value.__cause__ is error
    assert "timed out" not in str(caught.value)


def test_call_agent_times_out_without_waiting_for_python_thread():
    release = Event()
    started = Event()
    finished = Event()

    def agent(case, prompts):
        started.set()
        release.wait(timeout=1)
        finished.set()
        return "done"

    before = time.monotonic()
    try:
        with pytest.raises(AgentError, match="timed out after 0.05s"):
            call_agent(function_agent(agent), {}, {}, timeout_s=0.05)
        assert time.monotonic() - before < 0.75
        assert started.is_set()
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(timeout=1)


@pytest.mark.parametrize("kind", ["object", "circular"])
def test_call_agent_rejects_unserializable_output(kind):
    output = object() if kind == "object" else []
    if kind == "circular":
        output.append(output)

    def agent(case, prompts):
        return output

    with pytest.raises(AgentError, match="JSON-serializable") as caught:
        call_agent(function_agent(agent), {}, {}, timeout_s=1)

    assert isinstance(caught.value.__cause__, (TypeError, ValueError))


def test_command_agent_receives_case_and_prompts_on_stdin(tmp_path):
    script = tmp_path / "agent with spaces.py"
    script.write_text(
        "import json, sys\nprint(json.dumps(json.load(sys.stdin)))\n", encoding="utf-8"
    )
    case_input = {"question": "café", "nested": {"values": [1, False, None]}}
    prompts = {"system": "be helpful\nand concise", "tools": "no tools"}

    output = call_agent(
        command_agent([sys.executable, str(script)]), case_input, prompts, timeout_s=1
    )

    assert output == {"case": case_input, "prompts": prompts}


def test_command_agent_passes_literal_argv(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    argument = "; echo this must stay an argument"
    agent = command_agent([sys.executable, str(script), argument])

    assert agent({}, {}, 1) == [argument]


def test_command_agent_reports_nonzero_exit_and_stderr_tail(tmp_path):
    script = tmp_path / "agent.py"
    stderr = "omitted-prefix" + "x" * 600 + "failure at end"
    script.write_text(f"import sys\nsys.stderr.write({stderr!r})\nsys.exit(7)\n", encoding="utf-8")

    with pytest.raises(AgentError) as caught:
        call_agent(command_agent([sys.executable, str(script)]), {}, {}, timeout_s=1)

    assert "7" in str(caught.value)
    assert stderr[-500:] in str(caught.value)
    assert "omitted-prefix" not in str(caught.value)


@pytest.mark.parametrize("stdout", ["", "bad-json" + "x" * 300 + "omitted-tail"])
def test_command_agent_reports_invalid_json_and_stdout_prefix(tmp_path, stdout):
    script = tmp_path / "agent.py"
    script.write_text(f"import sys\nsys.stdout.write({stdout!r})\n", encoding="utf-8")

    with pytest.raises(AgentError, match="JSON") as caught:
        call_agent(command_agent([sys.executable, str(script)]), {}, {}, timeout_s=1)

    assert stdout[:200] in str(caught.value)
    assert "omitted-tail" not in str(caught.value)


def test_command_agent_times_out_sleeping_process(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text("import time\ntime.sleep(5)\nprint('null')\n", encoding="utf-8")
    before = time.monotonic()

    with pytest.raises(AgentError, match="timed out after 0.5s"):
        call_agent(command_agent([sys.executable, str(script)]), {}, {}, timeout_s=0.5)

    assert time.monotonic() - before < 2


def test_command_agent_reports_missing_executable(tmp_path):
    with pytest.raises(AgentError, match="command") as caught:
        call_agent(command_agent([str(tmp_path / "missing-command")]), {}, {}, timeout_s=1)

    assert isinstance(caught.value.__cause__, OSError)


def test_command_agent_decodes_output_as_utf8_regardless_of_locale(tmp_path):
    # Agents in any language print UTF-8 JSON; decoding must not depend on the OS locale.
    script = tmp_path / "agent.py"
    script.write_text(
        "import json, sys\n"
        "sys.stdout.buffer.write(json.dumps({'text': 'café ✓'}, ensure_ascii=False)"
        ".encode('utf-8'))\n",
        encoding="utf-8",
    )

    output = call_agent(command_agent([sys.executable, str(script)]), {}, {}, timeout_s=5)

    assert output == {"text": "café ✓"}
