"""Plan, run and publish a bounded evolution of the project's prompt files."""

import json
import sys
from dataclasses import asdict

from promptcheckup import __version__
from promptcheckup.checkup.evolve import evolution_cases, evolve
from promptcheckup.checkup.report import evolve_report
from promptcheckup.cli.checkup import planned_checkup_calls
from promptcheckup.cli.config import ConfigError, load_config
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
from promptcheckup.errors import errors_as


def evolution(args, *, llm=None) -> int:
    """Validate and confirm before running; publish only the last accepted prompts."""
    config = load_config(args.config)
    prompts = load_current_prompts(config)
    cases = project_cases(config)
    learning, holdout = _split(config, cases, args)
    llm = project_llm(config, commands="evolve") if llm is None else llm
    if not _confirmed(config, learning, holdout, args):
        return 3
    result = _run_project(config, cases, prompts, llm, args)
    _write_results(config, prompts, result, args)
    return 0 if result.accepted else 2


def _split(config, cases, args):
    with errors_as(ConfigError, {ValueError: str}):
        return evolution_cases(
            cases,
            rounds=args.rounds,
            holdout_share=args.holdout,
            confidence=config.run.confidence,
            min_pairs=config.run.min_pairs,
        )


def planned_evolve_calls(config, n_learning, n_holdout, rounds) -> dict:
    """Cold-cache estimate, counting current learning runs and held-out gates separately."""
    calls = planned_checkup_calls(config, n_learning, "propose", n_gate=n_holdout)
    return {kind: rounds * count for kind, count in calls.items()}


def _confirmed(config, learning, holdout, args):
    calls = planned_evolve_calls(config, len(learning), len(holdout), args.rounds)
    print(
        f"Planned: up to {calls['agent']} agent calls, {calls['llm']} LLM calls "
        f"(max_calls = {config.run.max_calls}; cache hits are free).",
        file=sys.stderr,
    )
    return confirm(args.yes)


def _run_project(config, cases, prompts, llm, args):
    with project_context(config.root), errors_as(ConfigError, {ValueError: str}):
        return evolve(
            project_agent(config),
            cases,
            prompts,
            llm,
            rounds=args.rounds,
            holdout_share=args.holdout,
            trial_command="evolve",
            **proposal_settings(config),
        )


def _write_results(config, originals, result, args):
    report = evolve_report(result, originals=originals, version=__version__)
    if result.accepted:
        write_prompts(config, originals, result.final_prompts)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    if args.json:
        args.json.write_text(
            json.dumps({**asdict(result), "version": __version__}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
