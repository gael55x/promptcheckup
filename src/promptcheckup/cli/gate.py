"""Assemble a project gate and write its report at the command-line boundary."""

import json
import sys
from dataclasses import asdict

from promptcheckup import __version__
from promptcheckup.checkup import evaluate, judge_mode, planned_calls, redacted_inputs, score_mode
from promptcheckup.checkup.ledger import record_trial, trials
from promptcheckup.checkup.report import gate_report
from promptcheckup.cli.config import load_config
from promptcheckup.cli.project import (
    confirm,
    project_agent,
    project_cache,
    project_cases,
    project_context,
    project_redactor,
)
from promptcheckup.cli.prompts import load_prompts
from promptcheckup.llm import LLM
from promptcheckup.runner import load_function, prompt_hash
from promptcheckup.runner.redaction import redacting_agent

EXIT_CODES = {"better": 0, "worse": 1, "inconclusive": 2}
SCORERS = {
    "score": lambda config, redact, cache: score_mode(load_function(config.scoring.score)),
    "judge": lambda config, redact, cache: _judge_scorer(config, redact, cache),
}


def gate(args) -> int:
    """Read inputs, confirm the call plan, evaluate and publish local artifacts."""
    config = load_config(args.config)
    prompts_a, prompts_b = load_prompts(config, old_ref=args.old_ref, old=args.old, new=args.new)
    cases = project_cases(config)
    if not _confirmed(config, len(cases), args.yes):
        return 3
    result = _evaluate_project(config, cases, prompts_a, prompts_b)
    hashes = {"A": prompt_hash(prompts_a), "B": prompt_hash(prompts_b)}
    ids = [case.id for case in cases]
    record_trial(
        config.root,
        case_ids=ids,
        prompt_a=hashes["A"],
        prompt_b=hashes["B"],
        decision=result.verdict.decision,
        confidence=config.run.confidence,
        command="gate",
    )
    _write_results(result, hashes, args, trials(config.root, ids), config.run.confidence)
    return EXIT_CODES[result.verdict.decision]


def _confirmed(config, count, yes) -> bool:
    calls = planned_calls(count, config.run.samples, config.scoring.mode)
    # Progress goes to stderr so stdout carries only the report.
    print(
        f"Planned: {calls['agent']} agent calls, {calls['judge']} judge calls "
        f"(max_calls = {config.run.max_calls}; cache hits are free).",
        file=sys.stderr,
    )
    return confirm(yes)


def _evaluate_project(config, cases, prompts_a, prompts_b):
    with project_context(config.root):
        redact = project_redactor(config)
        agent = redacting_agent(project_agent(config), redact)
        cache = project_cache(config)
        scorer = SCORERS[config.scoring.mode](config, redact, cache)
        return evaluate(
            agent,
            cases,
            prompts_a,
            prompts_b,
            scorer,
            samples=config.run.samples,
            timeout_s=config.agent.timeout_s,
            max_calls=config.run.max_calls,
            min_pairs=config.run.min_pairs,
            cache=cache,
            confidence=config.run.confidence,
        )


def _judge_scorer(config, redact, cache):
    # max_calls counts judgments; a retried 429 or 5xx is the same judgment, not a new one.
    llm = LLM(**asdict(config.llm))
    # Outputs were redacted before caching; only the case inputs still need it.
    return redacted_inputs(judge_mode(llm, config.scoring.criteria, cache=cache), redact)


def _write_results(result, hashes, args, count, confidence):
    text = gate_report(
        result, prompt_hashes=hashes, version=__version__, trial_count=count, confidence=confidence
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.json:
        data = {**asdict(result), "prompt_hashes": hashes, "version": __version__}
        args.json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
