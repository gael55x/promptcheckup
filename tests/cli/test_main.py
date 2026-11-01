import json
import os
import shlex
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from promptcheckup.cli.config import load_config
from promptcheckup.cli.main import main
from promptcheckup.data import load_cases

SOURCE = str(Path(__file__).resolve().parents[2] / "src")


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tiny_agent.py").write_text(
        "def run(case, prompts):\n    return int(prompts['system.md']) + case['index']\n"
        "def score(case, output):\n    return output - case['index']\n",
        encoding="utf-8",
    )
    (tmp_path / "promptcheckup.toml").write_text(
        """
[agent]
entry = "tiny_agent:run"
[prompts]
files = ["system.md"]
[cases]
paths = ["cases.jsonl"]
[scoring]
mode = "score"
score = "tiny_agent:score"
""",
        encoding="utf-8",
    )
    (tmp_path / "cases.jsonl").write_text(
        "".join(json.dumps({"id": str(i), "input": {"index": i}}) + "\n" for i in range(20)),
        encoding="utf-8",
    )
    (tmp_path / "old.md").write_text("0", encoding="utf-8")
    (tmp_path / "new.md").write_text("2", encoding="utf-8")
    (tmp_path / "system.md").write_text("0", encoding="utf-8")
    return tmp_path


def cli(project, *args, stdin="", cwd=None):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "promptcheckup.cli.main",
            "gate",
            "--config",
            str(project / "promptcheckup.toml"),
            *args,
        ],
        cwd=cwd or project,
        input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", os.defpath), "PYTHONPATH": SOURCE},
        timeout=1.8,
    )


def failure_lines(result):
    """Stderr without the call plan, which is progress rather than failure."""
    return [line for line in result.stderr.splitlines() if not line.startswith("Planned:")]


def file_args(project):
    return ["--old", str(project / "old.md"), "--new", str(project / "new.md")]


def test_gate_failed_majority_exits_inconclusive(project):
    (project / "tiny_agent.py").write_text(
        "def run(case, prompts):\n"
        "    if prompts['system.md'] == '2' and case['index'] >= 20:\n"
        "        raise ValueError('failed')\n"
        "    return 0.5 if prompts['system.md'] == '0' else 1\n"
        "def score(case, output):\n    return output\n",
        encoding="utf-8",
    )
    (project / "cases.jsonl").write_text(
        "".join(json.dumps({"id": str(i), "input": {"index": i}}) + "\n" for i in range(100)),
        encoding="utf-8",
    )
    result = cli(project, *file_args(project), "--yes")
    assert result.returncode == 2
    assert "160 of 400 runs failed; fix the errors and run again" in result.stdout


def test_gate_redacts_agent_errors_in_report_and_json(project):
    (project / "tiny_agent.py").write_text(
        'import json\ndef run(case, prompts):\n    raise ValueError("private-value")\n'
        "def score(case, output):\n    return 0\n"
        "def redact(value):\n"
        '    return json.loads(json.dumps(value).replace("private-value", "safe"))\n',
        encoding="utf-8",
    )
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text() + '\n[run]\nredact = "tiny_agent:redact"\n')
    result = cli(project, *file_args(project), "--yes", "--json", "result.json")
    assert result.returncode == 2
    assert "private-value" not in result.stdout + result.stderr
    assert "private-value" not in (project / "result.json").read_text()


def test_init_writes_three_templates_and_skips_existing_files(tmp_path, capsys):
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert load_config(tmp_path / "promptcheckup.toml").scoring.mode == "judge"
    assert len(load_cases(["cases/example.jsonl"], tmp_path)) == 3
    workflow = tmp_path / ".github/workflows/promptcheckup-gate.yml"
    text = workflow.read_text()
    for fragment in (
        "pull_request:",
        "prompts/system.md",
        "prompts/tools.md",
        "fetch-depth: 0",
        "--yes --out report.md",
        "gh pr comment",
        "--body-file report.md",
        "continue-on-error: true",
        "pull-requests: write",
    ):
        assert fragment in text
    assert "-e ." not in text  # the user's repository need not be a Python package
    (tmp_path / "promptcheckup.toml").write_text("keep this", encoding="utf-8")

    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "promptcheckup.toml").read_text() == "keep this"
    output = capsys.readouterr().out
    assert "Skipped promptcheckup.toml" in output
    assert "Skipped cases/example.jsonl" in output
    assert "Skipped .github/workflows/promptcheckup-gate.yml" in output


@pytest.mark.parametrize(
    ("new", "code", "headline"),
    [
        ("2", 0, "B is better:"),
        ("-2", 1, "B is worse:"),
        ("0", 2, "Not sure yet:"),
    ],
)
def test_gate_file_sources_report_and_json(project, new, code, headline):
    (project / "new.md").write_text(new, encoding="utf-8")
    result = cli(project, *file_args(project), "--yes", "--json", "result.json")

    assert result.returncode == code, result.stderr
    assert "80 agent calls" in result.stderr and "0 judge calls" in result.stderr
    assert result.stdout.startswith(headline)
    data = json.loads((project / "result.json").read_text())
    assert data["verdict"]["decision"] == {0: "better", 1: "worse", 2: "inconclusive"}[code]
    assert data["verdict"]["n"] == 20
    assert data["prompt_hashes"]["A"]
    assert data["version"]
    assert (project / ".promptcheckup/cache").is_dir()


def test_gate_resolves_project_from_config_and_writes_out(project, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    report = outside / "report.md"
    result = cli(project, *file_args(project), "--yes", "--out", str(report), cwd=outside)

    assert result.returncode == 0, result.stderr
    assert report.read_text().startswith("B is better:")
    assert "B is better:" not in result.stdout


@pytest.mark.parametrize("answer", ["n\n", "\n", ""])
def test_declined_or_closed_confirmation_returns_three_without_running(project, answer):
    result = cli(project, *file_args(project), stdin=answer)

    assert result.returncode == 3
    assert "Proceed? [y/N]" in result.stderr
    assert result.stdout == ""
    assert not (project / ".promptcheckup/cache").exists()


def test_confirmation_accepts_yes(project):
    result = cli(project, *file_args(project), stdin="y\n")

    assert result.returncode == 0, result.stderr


def test_bad_config_returns_three_and_single_line_stderr(project):
    (project / "promptcheckup.toml").write_text("[broken\n", encoding="utf-8")
    result = cli(project, *file_args(project), "--yes")

    assert result.returncode == 3
    assert len(failure_lines(result)) == 1
    assert "Traceback" not in result.stderr


def test_bad_agent_returns_three_and_single_line_stderr(project):
    (project / "tiny_agent.py").write_text(
        "raise RuntimeError('broken\\nimport')", encoding="utf-8"
    )
    result = cli(project, *file_args(project), "--yes")

    assert result.returncode == 3
    assert len(failure_lines(result)) == 1
    assert "broken import" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["--old", "old.md"],
        ["--old", "old.md", "new.md", "--new", "new.md"],
        ["--old-ref", "HEAD", "--new", "new.md"],
    ],
)
def test_invalid_prompt_sources_return_three(project, args):
    result = cli(project, *args, "--yes")

    assert result.returncode == 3
    assert len(failure_lines(result)) == 1


def git(project, *args):
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True, text=True, timeout=1
    )


def test_old_ref_reads_committed_prompt_and_compares_working_tree(project):
    git(project, "init", "--quiet")
    git(project, "add", "system.md")
    git(
        project,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "Old prompt",
    )
    (project / "system.md").write_text("2", encoding="utf-8")
    result = cli(project, "--old-ref", "HEAD", "--yes")

    assert result.returncode == 0, result.stderr
    assert "B is better:" in result.stdout


def test_missing_ref_is_a_single_line_config_error(project):
    git(project, "init", "--quiet")
    result = cli(project, "--old-ref", "missing-ref", "--yes")

    assert result.returncode == 3
    assert len(failure_lines(result)) == 1
    assert "git" in result.stderr


def test_gate_command_agent_runs_in_config_directory(project):
    (project / "command.py").write_text(
        "import json, sys\n"
        "data = json.load(sys.stdin)\n"
        "print(int(data['prompts']['system.md']) + data['case']['index'])\n",
        encoding="utf-8",
    )
    path = project / "promptcheckup.toml"
    path.write_text(
        path.read_text().replace(
            'entry = "tiny_agent:run"', f'cmd = "{shlex.quote(sys.executable)} command.py"'
        )
        + "\n[run]\nsamples = 1\nmin_pairs = 5\n",
        encoding="utf-8",
    )
    cases_path = project / "cases.jsonl"
    cases_path.write_text(
        "\n".join(cases_path.read_text().splitlines()[:5]) + "\n", encoding="utf-8"
    )
    outside = project / "outside"
    outside.mkdir()
    result = cli(project, *file_args(project), "--yes", cwd=outside)

    assert result.returncode == 0, result.stderr


def test_gate_redacts_outputs_before_cache_report_and_json(project):
    (project / "tiny_agent.py").write_text(
        "def run(case, prompts):\n"
        "    return {'value': int(prompts['system.md']) + case['index'], 'private': 'secret'}\n"
        "def score(case, output):\n    return output['value'] - case['index']\n"
        "def redact(value):\n    return {k: v for k, v in value.items() if k != 'private'}\n",
        encoding="utf-8",
    )
    path = project / "promptcheckup.toml"
    path.write_text(path.read_text() + '\n[run]\nredact = "tiny_agent:redact"\n', encoding="utf-8")
    result = cli(project, *file_args(project), "--yes", "--json", "result.json")

    assert result.returncode == 0, result.stderr
    assert "secret" not in result.stdout
    assert "secret" not in (project / "result.json").read_text()
    assert all(
        "secret" not in path.read_text()
        for path in (project / ".promptcheckup/cache").glob("*.json")
    )
    assert (project / ".promptcheckup/.gitignore").read_text() == "*\n"


def test_gate_llm_error_returns_three_without_credentials(project):
    path = project / "promptcheckup.toml"
    text = path.read_text().replace('mode = "score"', 'mode = "judge"')
    path.write_text(
        text + '\ncriteria = "Be correct."\n[llm]\nbase_url = "not-a-url"\n'
        'model = "test"\napi_key_env = ""\n',
        encoding="utf-8",
    )
    result = cli(project, *file_args(project), "--yes")

    assert result.returncode == 3
    assert len(failure_lines(result)) == 1
    assert "LLM" in result.stderr


def test_gate_missing_cases_returns_three(project):
    (project / "cases.jsonl").unlink()
    result = cli(project, *file_args(project), "--yes")

    assert result.returncode == 3
    assert "cases.paths" in result.stderr


def test_gate_cached_run_preserves_verdict_and_reduces_calls(project):
    first = cli(project, *file_args(project), "--yes")
    second = cli(project, *file_args(project), "--yes")

    assert first.returncode == second.returncode == 0
    assert "Calls: 0 agent" in second.stdout


def test_old_ref_rejects_git_options(project):
    result = cli(project, "--old-ref=--output=unexpected-file", "--yes")

    assert result.returncode == 3
    assert "--old-ref must not start with '-'" in result.stderr
    assert not (project / "unexpected-file").exists()


def test_explicit_prompt_files_map_by_configured_position(project):
    path = project / "promptcheckup.toml"
    path.write_text(
        path.read_text().replace('["system.md"]', '["system.md", "tools.md"]'), encoding="utf-8"
    )
    (project / "tiny_agent.py").write_text(
        "def run(case, prompts):\n"
        "    return int(prompts['system.md']) - int(prompts['tools.md'])\n"
        "def score(case, output):\n    return output\n",
        encoding="utf-8",
    )
    result = cli(project, "--old", "old.md", "new.md", "--new", "new.md", "old.md", "--yes")

    assert result.returncode == 0, result.stderr
    assert "B is better:" in result.stdout


@pytest.fixture
def judge_server():
    """A chat-completions endpoint that rate-limits its first request, then judges."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if len(requests) == 1:
                self._reply(429, {"error": "slow down"})
                return
            prompt = body["messages"][0]["content"]
            first, second = (
                int(prompt.split(f"<response_{n}>\n")[1].split("\n")[0]) for n in (1, 2)
            )
            winner = "tie" if first == second else "1" if first > second else "2"
            content = json.dumps({"winner": winner, "reason": "Higher is better."})
            self._reply(200, {"choices": [{"message": {"content": content}}]})

        def _reply(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1", requests
    server.shutdown()
    server.server_close()


def test_gate_judge_mode_retries_a_rate_limited_judge(project, judge_server):
    url, requests = judge_server
    path = project / "promptcheckup.toml"
    text = path.read_text().replace('mode = "score"', 'mode = "judge"')
    path.write_text(
        text + f'\ncriteria = "Higher is better."\n[llm]\nbase_url = "{url}"\n'
        'model = "local"\napi_key_env = ""\n',
        encoding="utf-8",
    )
    result = cli(project, *file_args(project), "--yes")

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("B is better: 20 of 20 cases improved")
    assert len(requests) == 121  # 120 judgments plus the one rate-limited attempt


def test_workflow_templates_install_the_pinned_pypi_release(tmp_path):
    assert main(["init", "--dir", str(tmp_path)]) == 0
    for name in ("promptcheckup-gate.yml", "promptcheckup-checkup.yml"):
        text = (tmp_path / ".github/workflows" / name).read_text()
        # The signed PyPI release, never whatever is on the default branch today.
        assert 'pip install "promptcheckup>=1,<2"' in text
        assert "git+" not in text
