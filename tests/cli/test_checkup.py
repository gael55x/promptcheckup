import json
from pathlib import Path

import pytest

from promptcheckup.cli.checkup import weekly
from promptcheckup.cli.main import _parser, main
from promptcheckup.data import Case, append_cases
from tests.checkup.test_checkup import llm_stub

ORIGINAL = "0\n" + "rule\n" * 9


@pytest.fixture
def project(tmp_path):
    # A unique module name avoids import caching between independent temporary projects.
    module = "weekly_" + tmp_path.name.replace("-", "_")
    (tmp_path / f"{module}.py").write_text(
        "from pathlib import Path\n"
        "def run(case, prompts):\n"
        '    return (case["index"] + int(prompts["system"].splitlines()[0])\n'
        '            + int(Path("level").read_text()))\n'
        'def score(case, output):\n    return output - case["index"]\n',
        encoding="utf-8",
    )
    (tmp_path / "level").write_text("0")
    (tmp_path / "system").write_text(ORIGINAL)
    (tmp_path / "promptcheckup.toml").write_text(
        f'[agent]\nentry = "{module}:run"\nexample_input = {{index = 0}}\n'
        '[prompts]\nfiles = ["system"]\n[cases]\npaths = ["cases/*.jsonl"]\n'
        f'[scoring]\nmode = "score"\nscore = "{module}:score"\n'
        '[run]\nsamples = 2\nmin_pairs = 5\nagent_version = "v1"\n',
        encoding="utf-8",
    )
    append_cases(tmp_path / "cases/data.jsonl", [Case(f"c{i}", {"index": i}) for i in range(5)])
    return tmp_path


def execute(project, *options, command="checkup", llm=None):
    args = _parser().parse_args(
        [command, "--config", str(project / "promptcheckup.toml"), "--yes", *options]
    )
    return weekly(args, llm=llm or llm_stub(rejected=True))


def test_checkup_cli_exit_codes_follow_drift(project, capsys):
    assert execute(project) == 0
    assert execute(project) == 0
    (project / "level").write_text("-2")
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text().replace('"v1"', '"v2"'))
    assert execute(project) == 1
    captured = capsys.readouterr()
    assert "Your agent got worse" in captured.out
    assert "Planned:" in captured.err and "Planned:" not in captured.out


@pytest.mark.parametrize("edit,changed", [("2", True), ("-2", False)])
def test_cli_writes_prompts_only_when_gate_passes(project, edit, changed):
    assert execute(project, llm=llm_stub(edit=edit)) == 0
    assert ((project / "system").read_text() != ORIGINAL) is changed


def test_cli_writes_report_and_proposal_markdown(project, capsys):
    report, proposal = project / "checkup.md", project / "proposal.md"
    assert (
        execute(project, "--out", str(report), "--proposal-out", str(proposal), llm=llm_stub()) == 0
    )
    assert report.read_text().startswith("A prompt change passed the gate")
    assert "Improve low scores." in proposal.read_text()
    assert "c0" in proposal.read_text() and "system" in proposal.read_text()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("edit,code", [("2", 0), ("-2", 2), ("0", 2)])
def test_propose_cli_exit_codes_and_no_snapshot(project, edit, code):
    assert execute(project, command="propose", llm=llm_stub(edit=edit)) == code
    assert not (project / ".promptcheckup/snapshots").exists()


def test_cli_budget_is_inconclusive_and_cannot_write_prompts(project, capsys):
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text() + "max_calls = 10\n")
    assert execute(project, llm=llm_stub()) == 2
    assert (project / "system").read_text() == ORIGINAL
    assert "budget" in capsys.readouterr().out.splitlines()[0]


def test_checkup_cli_generates_cases_only_when_no_files_match(project):
    (project / "cases/data.jsonl").unlink()
    assert execute(project) == 0
    assert (project / "cases/starter.jsonl").exists()


def test_cli_empty_matching_file_is_not_treated_as_missing_cases(project):
    from promptcheckup.cli.config import ConfigError

    (project / "cases/data.jsonl").write_text("")
    with pytest.raises(ConfigError, match="cases"):
        execute(project)
    assert not (project / "cases/starter.jsonl").exists()


def test_checkup_commands_require_configured_llm_even_in_score_mode(project, capsys):
    assert main(["checkup", "--config", str(project / "promptcheckup.toml"), "--yes"]) == 3
    assert "llm" in capsys.readouterr().err


def test_checkup_cli_confirmation_uses_stderr_and_can_decline(project, capsys):
    args = _parser().parse_args(["checkup", "--config", str(project / "promptcheckup.toml")])
    # EOF on a real empty file is a declined confirmation, without mocking input().
    import sys
    from contextlib import ExitStack

    with ExitStack() as stack:
        previous = sys.stdin
        stack.callback(setattr, sys, "stdin", previous)
        sys.stdin = stack.enter_context(Path("/dev/null").open())
        assert weekly(args, llm=llm_stub()) == 3
    captured = capsys.readouterr()
    assert "Proceed? [y/N]" in captured.err and captured.out == ""
    assert not (project / ".promptcheckup").exists()


def test_checkup_init_writes_weekly_workflow_and_never_overwrites_it(tmp_path):
    assert main(["init", "--dir", str(tmp_path)]) == 0
    workflow = tmp_path / ".github/workflows/promptcheckup-checkup.yml"
    text = workflow.read_text()
    for fragment in (
        "cron:",
        "workflow_dispatch:",
        "actions/cache",
        ".promptcheckup/snapshots",
        "hashFiles(",
        "--yes --out checkup.md --proposal-out proposal.md",
        "gh issue list --label promptcheckup --state open",
        "gh issue create",
        "gh issue comment",
        "--body-file checkup.md",
        "git diff",
        "git commit",
        "git push",
        "gh pr create",
        "--body-file proposal.md",
        "contents: write",
        "issues: write",
        "pull-requests: write",
        "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}",  # check-ups always call the LLM
    ):
        assert fragment in text
    assert "merge" not in text
    workflow.write_text("keep me")
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert workflow.read_text() == "keep me"


def test_cli_json_contains_budget_and_provenance(project):
    output = project / "result.json"
    assert execute(project, "--json", str(output)) == 0
    data = json.loads(output.read_text())
    assert data["prompts_hash"] and data["version"]
    assert data["calls"] == 12 and not data["budget_exhausted"]


def test_weekly_workflow_requires_both_reports_before_opening_pr(tmp_path):
    assert main(["init", "--dir", str(tmp_path)]) == 0
    text = (tmp_path / ".github/workflows/promptcheckup-checkup.yml").read_text()
    assert "hashFiles('checkup.md') != '' && hashFiles('proposal.md') != ''" in text


def test_known_drift_retains_exit_one_even_if_later_llm_budget_runs_out(project, capsys):
    assert execute(project) == 0
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text().replace('"v1"', '"v2"') + "max_calls = 10\n")
    (project / "level").write_text("-2")
    assert execute(project) == 1
    assert "budget" in capsys.readouterr().out
    assert (project / "system").read_text() == ORIGINAL


def test_weekly_case_globs_are_recursive_like_the_existing_case_loader(project):
    nested = project / "cases/nested/deeper"
    nested.mkdir(parents=True)
    (project / "cases/data.jsonl").rename(nested / "data.jsonl")
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text().replace("cases/*.jsonl", "cases/**/*.jsonl"))
    assert execute(project) == 0
    assert not (project / "cases/starter.jsonl").exists()
