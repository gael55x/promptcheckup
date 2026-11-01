import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from promptcheckup.cli.config import load_config
from promptcheckup.cli.main import main
from promptcheckup.cli.project import project_context

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/connect"
FENCES = re.compile(r"```([^\n]*)\n(.*?)```", re.DOTALL)


def example(tmp_path, name):
    return Path(shutil.copytree(EXAMPLES / name, tmp_path / name))


def test_plain_example_doctor(tmp_path, capsys):
    path = example(tmp_path, "plain")
    assert main(["doctor", "--config", str(path / "promptcheckup.toml"), "--no-llm"]) == 0
    output = capsys.readouterr().out
    assert "warn" not in output and "fail" not in output


def test_node_example_doctor(tmp_path, capsys):
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    path = example(tmp_path, "node_cmd")
    assert main(["doctor", "--config", str(path / "promptcheckup.toml"), "--no-llm"]) == 0
    output = capsys.readouterr().out
    assert "warn" not in output and "fail" not in output


@pytest.mark.parametrize("name", ["openai_sdk", "anthropic_sdk"])
def test_sdk_examples_compile_and_load_without_importing_sdk(name):
    path = EXAMPLES / name
    config = load_config(path / "promptcheckup.toml")
    agent = path / (config.agent.entry.partition(":")[0] + ".py")
    compile(agent.read_text(), str(agent), "exec")
    assert config.scoring.mode == "judge" and config.llm.model
    assert all(file.is_file() for file in config.prompts.files)


def test_guide_fenced_examples_match_real_files():
    guide = (ROOT / "docs/connect-your-agent.md").read_text()
    fences = list(FENCES.finditer(guide))
    assert fences
    for name in ("test_prompt_change.py", "test_no_lookahead.py"):
        assert f"[examples/connect/testing/{name}]" in guide
    for fence in fences:
        # Each fence is immediately preceded by a link naming its complete source file.
        prefix = guide[: fence.start()].rstrip().splitlines()[-1]
        match = re.fullmatch(r"\[(examples/connect/[^\]]+)\]\(\.\./[^)]+\)", prefix)
        assert match, f"code block lacks an example file: {prefix}"
        assert fence[2] == (ROOT / match[1]).read_text(), match[1]


def test_plain_first_gate_script(tmp_path):
    path = example(tmp_path, "plain")
    env = {"PYTHONPATH": str(ROOT / "src")}
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    result = subprocess.run(
        ["sh", str(path / "first-gate.sh")], capture_output=True, text=True, env=env, timeout=10
    )
    assert result.returncode == 0, result.stderr
    assert "B is better:" in result.stdout
    assert json.loads((path / "result.json").read_text())["verdict"]["decision"] == "better"


def load_example(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_before_after_adapter_and_recording_redaction(tmp_path):
    path = example(tmp_path, "plain")
    with project_context(path):
        before = load_example(path / "before.py")
        after = load_example(path / "after.py")
        recording = load_example(path / "recording.py")
        case = {"question": "Ada", "email": "private@example.invalid"}
        assert before.run(case)["answer"] == "Hello, Ada!"
        assert after.run(case, {"prompts/system.md": "Goodbye,"})["answer"] == "Goodbye, Ada!"
        assert recording.handle(case)["answer"] == "Hello, Ada!"
    contents = (path / "cases/recorded.jsonl").read_text()
    assert "private@example.invalid" not in contents and "recorded" in contents


@pytest.mark.parametrize(
    "name,filename", [("plain", "plain_agent.py"), ("node_cmd", "node_score.py")]
)
def test_example_scores_handle_unexpected_json_outputs(name, filename):
    module = load_example(EXAMPLES / name / filename)
    assert all(module.score({"question": "Ada"}, output) == 0 for output in (None, [], "wrong", 42))


def test_doctor_sample_matches_plain_run(tmp_path, capsys):
    path = example(tmp_path, "plain")
    with project_context(path):
        assert main(["doctor", "--no-llm"]) == 0
    output = re.sub(r"ran in [0-9.]+s", "ran in 0.001s", capsys.readouterr().out)
    assert output == (EXAMPLES / "doctor-output.txt").read_text()


def test_node_protocol_files(tmp_path):
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    path = example(tmp_path, "node_cmd")
    result = subprocess.run(
        ["node", "agent.mjs"],
        cwd=path,
        input=(path / "input.json").read_text(),
        capture_output=True,
        text=True,
        timeout=10,  # an upper bound: a cold CI runner can take over a second to start node
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == (path / "output.json").read_text()


@pytest.mark.parametrize("name", ["test_prompt_change", "test_no_lookahead"])
def test_testing_guide_examples_run(name):
    module = load_example(EXAMPLES / "testing" / f"{name}.py")
    getattr(module, name)()
