import json

import pytest

from promptcheckup.checkup.ledger import trials
from promptcheckup.cli.checkup import weekly
from promptcheckup.cli.config import load_config
from promptcheckup.cli.evolve import evolution, planned_evolve_calls
from promptcheckup.cli.main import _parser, main
from promptcheckup.data import append_cases
from tests.checkup.test_evolve import PROMPTS, cases, proposals


@pytest.fixture
def project(tmp_path):
    module = "evolve_" + tmp_path.name.replace("-", "_")
    (tmp_path / f"{module}.py").write_text(
        "def run(case, prompts):\n"
        '    version = int(prompts["system"].splitlines()[0].split(": ")[1])\n'
        '    return min(version, 2) * case["weight"]\n'
        "def score(case, output):\n    return output\n",
        encoding="utf-8",
    )
    (tmp_path / "system").write_text(PROMPTS["system"])
    (tmp_path / "promptcheckup.toml").write_text(
        f'[agent]\nentry = "{module}:run"\n[prompts]\nfiles = ["system"]\n'
        '[cases]\npaths = ["cases.jsonl"]\n'
        f'[scoring]\nmode = "score"\nscore = "{module}:score"\n'
        "[run]\nsamples = 1\nmin_pairs = 5\n",
        encoding="utf-8",
    )
    append_cases(tmp_path / "cases.jsonl", cases())
    return tmp_path


def args(project, *options, command="evolve", yes=True):
    return _parser().parse_args(
        [
            command,
            "--config",
            str(project / "promptcheckup.toml"),
            *(["--yes"] if yes else []),
            *options,
        ]
    )


def ledger(project):
    return [
        json.loads(line)
        for line in (project / ".promptcheckup/trials.jsonl").read_text().splitlines()
    ]


def test_evolve_cli_writes_accepted_prompts_reports_json_and_trials(project, capsys):
    out, data = project / "evolve.md", project / "result.json"
    assert evolution(args(project, "--out", str(out), "--json", str(data)), llm=proposals()) == 0
    assert (project / "system").read_text().startswith("version: 2\n")
    assert out.read_text().startswith("Evolved: 2 of 3")
    payload = json.loads(data.read_text())
    assert payload["accepted"] == 2 and payload["version"]
    assert len(payload["holdout_ids"]) == 20
    rows = ledger(project)
    assert len(rows) == 3
    assert all(row["command"] == "evolve" and row["confidence"] == 0.99 for row in rows)
    assert rows[-1]["decision"] == "inconclusive"
    assert rows[1]["prompt_a"] == rows[0]["prompt_b"]
    assert trials(project, payload["holdout_ids"]) == 3
    assert trials(project, [c.id for c in cases()]) == 0
    captured = capsys.readouterr()
    assert captured.out == "" and "300 agent calls, 10 LLM calls" in captured.err


def test_evolve_cli_does_not_touch_prompt_file_if_no_round_passes(project, capsys):
    path = project / "system"
    before = path.stat().st_mtime_ns
    assert evolution(args(project), llm=proposals(edit="version: -1\n")) == 2
    assert path.read_text() == PROMPTS["system"] and path.stat().st_mtime_ns == before
    assert "No improvement" in capsys.readouterr().out
    assert ledger(project)[0]["decision"] == "worse"


def test_evolve_cli_decline_returns_three_without_running(project, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda: "n")
    assert evolution(args(project, yes=False), llm=proposals()) == 3
    assert "Proceed?" in capsys.readouterr().err
    assert not (project / ".promptcheckup").exists()


def test_evolve_cli_too_few_holdout_cases_has_one_line_error(project, capsys):
    (project / "cases.jsonl").unlink()
    append_cases(project / "cases.jsonl", cases()[20:])
    assert main(["evolve", "--config", str(project / "promptcheckup.toml"), "--yes"]) == 3
    assert capsys.readouterr().err == (
        "promptcheckup: need at least 5 held-out cases, got 0; add cases or raise holdout_share\n"
    )


@pytest.mark.parametrize("options", [["--rounds", "0"], ["--holdout", "1"]])
def test_evolve_cli_invalid_options_are_one_line_errors(project, capsys, options):
    assert main(["evolve", "--config", str(project / "promptcheckup.toml"), *options]) == 3
    assert len(capsys.readouterr().err.splitlines()) == 1


def test_evolve_cli_requires_existing_cases_without_starter(project, capsys):
    (project / "cases.jsonl").unlink()
    assert main(["evolve", "--config", str(project / "promptcheckup.toml"), "--yes"]) == 3
    assert "cases" in capsys.readouterr().err
    assert not (project / "cases/starter.jsonl").exists()


def test_evolve_cli_uses_configured_base_confidence(project):
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text() + "confidence = 0.9\n")
    assert evolution(args(project, "--rounds", "2"), llm=proposals()) == 0
    assert all(row["confidence"] == 0.95 for row in ledger(project))


def test_evolve_cli_budget_writes_only_last_complete_accepted_round(project):
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text() + "max_calls = 99\n")
    data = project / "result.json"
    assert evolution(args(project, "--json", str(data)), llm=proposals()) == 0
    result = json.loads(data.read_text())
    assert result["budget_exhausted"] and result["accepted"] == len(result["rounds"]) == 1
    assert (project / "system").read_text().startswith("version: 1\n")
    assert len(ledger(project)) == 2  # partial gate is still a trial, never an accepted round
    assert ledger(project)[-1]["decision"] == "inconclusive"


@pytest.mark.parametrize("command", ["checkup", "propose"])
@pytest.mark.parametrize("edit,decision", [("version: 1\n", "better"), ("version: -1\n", "worse")])
def test_weekly_records_accepted_and_failed_gated_proposals(project, command, edit, decision):
    weekly(args(project, command=command), llm=proposals(edit=edit))
    rows = ledger(project)
    assert len(rows) == 1 and rows[0]["command"] == command
    assert rows[0]["confidence"] == 0.95 and rows[0]["decision"] == decision
    assert trials(project, [c.id for c in cases()]) == 1


def test_weekly_does_not_record_ungated_rejected_edit(project):
    weekly(args(project, command="propose"), llm=proposals(edit=PROMPTS["system"]))
    assert not (project / ".promptcheckup/trials.jsonl").exists()


@pytest.mark.parametrize(
    "mode,expected", [("score", {"agent": 105, "llm": 6}), ("judge", {"agent": 210, "llm": 291})]
)
def test_planned_evolve_calls_uses_learning_and_holdout_counts(project, mode, expected):
    from dataclasses import replace

    config = load_config(project / "promptcheckup.toml")
    config = replace(
        config,
        scoring=replace(config.scoring, mode=mode),
        run=replace(config.run, samples=2 if mode == "judge" else 1),
    )
    assert planned_evolve_calls(config, 5, 15, 3) == expected


def test_evolve_main_dispatch_uses_only_injected_llm(project, capsys, monkeypatch):
    config = project / "promptcheckup.toml"
    config.write_text(config.read_text() + '[llm]\nbase_url = "stub"\nmodel = "stub"\n')
    monkeypatch.setattr("promptcheckup.cli.project.LLM", lambda **settings: proposals())
    assert main(["evolve", "--config", str(config), "--rounds", "1", "--yes"]) == 0
    assert "Evolved: 1 of 1" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["checkup", "propose"])
def test_weekly_honours_base_confidence_for_borderline_proposals(project, command):
    from dataclasses import replace

    config = project / "promptcheckup.toml"
    config.write_text(config.read_text() + "confidence = 0.99\n")
    (project / "cases.jsonl").unlink()
    append_cases(
        project / "cases.jsonl",
        [replace(case, input={**case.input, "weight": i - 15.5}) for i, case in enumerate(cases())],
    )
    result = project / "weekly.json"
    weekly(args(project, "--json", str(result), command=command), llm=proposals())
    payload = json.loads(result.read_text())
    assert payload["proposal_verdict"]["decision"] == "inconclusive"
    assert ledger(project)[0]["confidence"] == 0.99
    config.write_text(config.read_text().replace("confidence = 0.99", "confidence = 0.9"))
    weekly(args(project, "--json", str(result), command=command), llm=proposals())
    assert json.loads(result.read_text())["proposal_verdict"]["decision"] == "better"
    assert [row["confidence"] for row in ledger(project)] == [0.99, 0.9]
