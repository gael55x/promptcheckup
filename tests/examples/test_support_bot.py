"""The shipped judge quickstart, with no provider calls."""

import json
import re
from pathlib import Path

from promptcheckup.checkup.evaluate import evaluate, judge_mode
from promptcheckup.cli.config import load_config
from promptcheckup.cli.main import main
from promptcheckup.cli.project import project_agent, project_cases, project_context

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/support-bot"


def policy_judge(prompt):
    answers = [
        json.loads(prompt.split(f"<response_{side}>\n")[1].split(f"\n</response_{side}>")[0])
        for side in (1, 2)
    ]
    preference = tuple("Policy" in output["answer"] for output in answers)
    winner = {(False, True): "2", (True, False): "1"}.get(preference, "tie")
    return json.dumps({"winner": winner, "reason": "Prefer the specific policy citation."})


def test_support_bot_doctor_without_llm(capsys):
    assert main(["doctor", "--config", str(EXAMPLE / "promptcheckup.toml"), "--no-llm"]) == 0
    output = capsys.readouterr().out
    assert "ok    agent" in output
    assert "ok    prompt-use" in output
    assert "skip  llm" in output


def test_support_bot_gate_is_better_with_reliable_self_check():
    config = load_config(EXAMPLE / "promptcheckup.toml")
    prompts = [
        {"prompts/system.md": (EXAMPLE / name).read_text()}
        for name in ("prompts/old-system.md", "prompts/system.md")
    ]
    with project_context(EXAMPLE):
        result = evaluate(
            project_agent(config),
            project_cases(config),
            *prompts,
            judge_mode(policy_judge, config.scoring.criteria),
            samples=config.run.samples,
            timeout_s=1,
            max_calls=config.run.max_calls,
            min_pairs=config.run.min_pairs,
            cache=None,
        )
    assert result.verdict.decision == "better"
    assert result.verdict.n == 30
    assert result.scores.self_check.reliable is True
    assert result.scores.self_check.consistency == 1
    assert result.replay.calls == 120
    assert result.scores.judge_calls == 180
    assert not result.errors and not result.budget_exhausted


def test_support_bot_readme_results_match_shipped_reports():
    readme = (EXAMPLE / "README.md").read_text()
    rows = re.findall(
        r"^\| \[[^\]]+\]\((results/[^)]+)\) \| (.+) \| (.+) \| (.+) \|$", readme, re.MULTILINE
    )
    assert {name for name, *_ in rows} == {
        f"results/report-claude-{model}-5-5.md" for model in ("haiku", "sonnet", "opus")
    }
    for name, verdict, interval, check in rows:
        report = (EXAMPLE / name).read_text()
        assert verdict == report.splitlines()[0]
        assert f"95% CI {interval}" in verdict
        assert f"Self-check: {check}." in report


def test_support_bot_readme_shows_unchanged_prompts():
    readme = (EXAMPLE / "README.md").read_text()
    for name in ("old-system.md", "system.md"):
        assert (EXAMPLE / "prompts" / name).read_text().strip() in readme
