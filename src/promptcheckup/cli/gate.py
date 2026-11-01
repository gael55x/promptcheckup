"""Assemble a project gate and write its report at the command-line boundary."""

import json
import shlex
import sys
from contextlib import ExitStack, chdir, contextmanager
from dataclasses import asdict, replace
from pathlib import Path

from promptcheckup import __version__
from promptcheckup.checkup import evaluate, judge_mode, planned_calls, score_mode
from promptcheckup.checkup.report import gate_report
from promptcheckup.cli.config import ConfigError, load_config
from promptcheckup.cli.init import write_new
from promptcheckup.cli.prompts import load_prompts
from promptcheckup.data import CaseError, load_cases
from promptcheckup.errors import errors_as, fallback
from promptcheckup.llm import LLM
from promptcheckup.runner import (
    AgentError,
    Cache,
    command_agent,
    load_function,
    prompt_hash,
    python_agent,
)

EXIT_CODES = {"better": 0, "worse": 1, "inconclusive": 2}
SCORERS = {
    "score": lambda config, redact, cache: score_mode(load_function(config.scoring.score)),
    "judge": lambda config, redact, cache: _judge_scorer(config, redact, cache),
}


def gate(args) -> int:
    """Read inputs, confirm the call plan, evaluate and publish local artifacts."""
    config = load_config(args.config)
    prompts_a, prompts_b = load_prompts(config, old_ref=args.old_ref, old=args.old, new=args.new)
    cases = _load_cases(config)
    if not _confirmed(config, len(cases), args.yes):
        return 3
    result = _evaluate_project(config, cases, prompts_a, prompts_b)
    hashes = {"A": prompt_hash(prompts_a), "B": prompt_hash(prompts_b)}
    _write_results(result, hashes, args)
    return EXIT_CODES[result.verdict.decision]


def _load_cases(config):
    with errors_as(ConfigError, {CaseError: lambda error: f"cases.paths: {error}"}):
        patterns = [str(path.relative_to(path.anchor)) for path in config.cases.paths]
        cases = load_cases(patterns, Path(config.root.anchor))
    if not cases:
        raise ConfigError("cases.paths must contain at least one case")
    return cases


def _confirmed(config, count, yes) -> bool:
    calls = planned_calls(count, config.run.samples, config.scoring.mode)
    # Progress goes to stderr so stdout carries only the report.
    print(
        f"Planned: {calls['agent']} agent calls, {calls['judge']} judge calls "
        f"(max_calls = {config.run.max_calls}; cache hits are free).",
        file=sys.stderr,
    )
    if yes:
        return True
    print("Proceed? [y/N] ", end="", file=sys.stderr, flush=True)
    answer = fallback(input, lambda error: "", EOFError)
    return answer.strip().lower() in ("y", "yes")


def _evaluate_project(config, cases, prompts_a, prompts_b):
    with _project_context(config.root):
        redact = _redactor(config)
        agent = _redacting_agent(_agent(config), redact)
        cache = _cache(config)
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
        )


@contextmanager
def _project_context(root):
    with ExitStack() as stack:
        stack.enter_context(chdir(root))
        sys.path.insert(0, str(root))
        stack.callback(sys.path.remove, str(root))
        yield


def _agent(config):
    if config.agent.entry.strip():
        return python_agent(config.agent.entry)
    with errors_as(ConfigError, {ValueError: lambda error: f"agent.cmd: {error}"}):
        return command_agent(shlex.split(config.agent.cmd))


def _redactor(config):
    return load_function(config.run.redact) if config.run.redact else None


def _redacting_agent(agent, redact):
    if redact is None:
        return agent
    return lambda case, prompts, timeout_s: _redacted(redact, agent(case, prompts, timeout_s))


def _redacted(redact, value):
    with errors_as(AgentError, {Exception: lambda error: f"run.redact failed: {error}"}):
        return redact(value)


def _judge_scorer(config, redact, cache):
    # max_calls counts judgments; a retried 429 or 5xx is the same judgment, not a new one.
    llm = LLM(**asdict(config.llm))
    scorer = judge_mode(llm, config.scoring.criteria, cache=cache)
    if redact is None:
        return scorer
    # Outputs were redacted before caching; only the case inputs still need it.
    return lambda cases, replay, **settings: scorer(
        [replace(case, input=_redacted(redact, case.input)) for case in cases], replay, **settings
    )


def _cache(config):
    directory = config.root / ".promptcheckup"
    write_new(directory / ".gitignore", "*\n")
    # A newly configured redactor must never reuse previously unredacted outputs.
    version = json.dumps([config.run.agent_version, config.run.redact])
    return Cache(directory / "cache", ttl_hours=config.run.cache_ttl_hours, agent_version=version)


def _write_results(result, hashes, args):
    text = gate_report(result, prompt_hashes=hashes, version=__version__)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.json:
        data = {**asdict(result), "prompt_hashes": hashes, "version": __version__}
        args.json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
