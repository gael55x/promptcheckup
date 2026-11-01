"""Assemble weekly checks and write only proposals that passed the paired gate."""

import json
import sys
from dataclasses import asdict

from promptcheckup import __version__
from promptcheckup.checkup.checkup import checkup, propose_only
from promptcheckup.checkup.evaluate import planned_calls
from promptcheckup.checkup.report import checkup_report, proposal_report
from promptcheckup.cli.config import load_config
from promptcheckup.cli.project import (
    confirm,
    project_agent,
    project_cases,
    project_context,
    project_llm,
    proposal_settings,
    write_prompts,
)
from promptcheckup.cli.prompts import load_current_prompts

RUNNERS = {"checkup": checkup, "propose": propose_only}
EXIT_CODES = {
    "checkup": lambda result: int(
        result.drift is not None and result.drift.verdict.decision == "worse"
    ),
    "propose": lambda result: 0 if result.proposal is not None else 2,
}
STATUS_CODES = (
    (lambda r: r.all_current_failed, 3),
    (lambda r: r.drift is not None and r.drift.verdict.decision == "worse", 1),
    (lambda r: r.budget_exhausted or bool(r.errors), 2),
    (lambda r: r.drift is not None and r.drift.verdict.ci_low is None, 2),
    (lambda r: r.drift is not None and bool(r.drift.errors), 2),
)


def weekly(args, *, llm=None) -> int:
    """Read, confirm, run and publish a weekly check-up or a proposal-only check."""
    config = load_config(args.config)
    prompts = load_current_prompts(config)
    cases = project_cases(config, allow_none=args.command == "checkup")
    llm = project_llm(config) if llm is None else llm
    if not _confirmed(config, len(cases), args):
        return 3
    result = _run_project(config, cases, prompts, llm, args.command)
    _write_results(config, prompts, result, args)
    return _exit_code(result, args.command)


def _exit_code(result, command):
    return next(
        (code for predicate, code in STATUS_CODES if predicate(result)),
        EXIT_CODES[command](result),
    )


def planned_checkup_calls(config, n_cases, command, *, n_gate=None) -> dict:
    """Cold-cache estimate for a check-up or proposal, including starter cases when needed."""
    n = n_cases or 20
    gate = planned_calls(n if n_gate is None else n_gate, config.run.samples, config.scoring.mode)
    drift_calls = (
        planned_calls(n, config.run.samples, config.scoring.mode)["judge"]
        if command == "checkup"
        else 0
    )
    grade_calls = n if config.scoring.mode == "judge" else 0
    llm_calls = gate["judge"] + drift_calls + grade_calls + 2 + int(not n_cases)
    agent_calls = n * config.run.samples + gate["agent"]
    return {"agent": agent_calls, "llm": llm_calls}


def _confirmed(config, count, args):
    calls = planned_checkup_calls(config, count, args.command)
    print(
        f"Planned: up to {calls['agent']} agent calls, {calls['llm']} LLM calls "
        f"(max_calls = {config.run.max_calls}; cache hits are free).",
        file=sys.stderr,
    )
    return confirm(args.yes)


def _run_project(config, cases, prompts, llm, command):
    with project_context(config.root):
        return RUNNERS[command](
            project_agent(config),
            cases,
            prompts,
            llm,
            trial_command=command,
            holdout_share=config.run.holdout,
            **proposal_settings(config),
        )


def _write_results(config, originals, result, args):
    report = checkup_report(result, version=__version__, confidence=config.run.confidence)
    proposal = proposal_report(
        result, originals=originals, version=__version__, confidence=config.run.confidence
    )
    _write_prompts(config, originals, result)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    if args.proposal_out:
        args.proposal_out.write_text(proposal, encoding="utf-8")
    if args.json:
        data = {**asdict(result), "version": __version__}
        args.json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def _write_prompts(config, originals, result):
    if result.proposal is None:
        return
    write_prompts(config, originals, result.proposal.prompts)
