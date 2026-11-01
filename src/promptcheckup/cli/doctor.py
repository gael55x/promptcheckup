"""Check a project's wiring with two real agent calls and at most one grade.

Each check reads the facts earlier checks established and returns its result with any
new facts. A check whose prerequisites failed is skipped and does no work.
"""

import json
import math
import os
import sys
from collections.abc import Callable
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from time import monotonic
from typing import Any

from promptcheckup.checkup.evaluate import planned_calls
from promptcheckup.cli.checkup import planned_checkup_calls
from promptcheckup.cli.config import Config, ConfigError, load_config
from promptcheckup.cli.project import (
    project_agent,
    project_cases,
    project_context,
    project_redactor,
)
from promptcheckup.cli.prompts import load_current_prompts
from promptcheckup.errors import errors_as, fallback
from promptcheckup.llm import LLM, LLMError, grade
from promptcheckup.runner import Agent, AgentError, call_agent, load_function

# Replaces every prompt for the second call. Appending would miss an agent that uses its
# prompts correctly but only reacts to part of them.
PROBE = "promptcheckup doctor probe: this text replaces your prompt for one diagnostic call."
PROBE_FAILED = (
    "the agent uses the supplied prompts, but failed when they were replaced with probe text "
    "(real runs never send it)"
)
PROMPT_WARNING = (
    "the output did not change when the prompts changed. Does your agent use the `prompts` "
    "argument instead of reading prompt files itself? See docs/connect-your-agent.md"
)
CASE_WARNINGS = (
    (
        lambda count, minimum: count == 0,
        lambda minimum: (
            "no cases yet: `promptcheckup checkup` will generate 20 starter cases, "
            "or record real ones with `@record`"
        ),
    ),
    (
        lambda count, minimum: count < minimum,
        lambda minimum: (
            f'every verdict will be "not sure yet" until there are at least {minimum} '
            "cases (run.min_pairs); record more with `@record`"
        ),
    ),
)
FIXES = {
    "config": "fix the config selected by --config",
    "prompts": "check prompts.files paths and file permissions",
    "cases": "fix cases.paths or the named case file",
    "agent": "check agent.entry/cmd and timeout_s; see docs/connect-your-agent.md",
    "prompt-use": "make the agent accept supplied prompt text; see docs/connect-your-agent.md",
    "score": "make scoring.score return a finite number for any output",
    "llm": "check [llm] and the provider error, then rerun doctor",
    "budget": "check run.samples and run.max_calls",
}
LLM_RULES = (
    (lambda llm: bool(llm.base_url.strip()), "llm.base_url is required"),
    (lambda llm: bool(llm.model.strip()), "llm.model is required"),
)
REPORTED_ERRORS = (ConfigError, AgentError, LLMError, OSError, UnicodeError)


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok, warn, fail or skip
    message: str


@dataclass(frozen=True)
class Facts:
    """What earlier checks established. Later checks read it and never change it."""

    args: Any
    llm: Callable[[str], str] | None = None  # injected judge; None builds one from [llm]
    config: Config | None = None
    prompts: dict[str, str] | None = None
    cases: list | None = None
    input: Any = None
    agent: Agent | None = None
    redact: Callable[[Any], Any] | None = None
    output: Any = None


def doctor(args, *, llm=None) -> int:
    """Report ordered checks without confirmation, caching, or writes to project files."""
    config_check, facts = _attempt("config", _config, Facts(args, llm))
    print(_cost(facts), flush=True)
    results = {"config": _printed(config_check)}
    for name, prerequisites, check in CHECKS:
        result, facts = _run_check(name, prerequisites, check, facts, results)
        results[name] = _printed(result)
    return _exit_code(results.values())


def _run_check(name, prerequisites, check, facts, results) -> tuple[Check, Facts]:
    blocked = next(
        (reason for dep, reason in prerequisites if results[dep].status in BLOCKING), None
    )
    if blocked is not None:
        return Check(name, "skip", blocked), facts
    return _attempt(name, check, facts)


def _attempt(name, check, facts) -> tuple[Check, Facts]:
    return fallback(
        lambda: check(facts),
        lambda error: (Check(name, "fail", f"{_one_line(error)}; {FIXES[name]}"), facts),
        *REPORTED_ERRORS,
    )


def _cost(facts) -> str:
    config = facts.config
    judge = int(config is not None and config.llm is not None and not facts.args.no_llm)
    return f"doctor makes 2 agent calls and {judge} judge call" + ("" if judge == 1 else "s")


def _printed(check) -> Check:
    print(f"{check.status:<4}  {check.name:<10} {_one_line(check.message)}", flush=True)
    return check


def _exit_code(results) -> int:
    failures = [f"{check.name}: {check.message}" for check in results if check.status == "fail"]
    if failures:
        print(f"promptcheckup: {_one_line('; '.join(failures))}", file=sys.stderr)
    return 3 if failures else 0


def _one_line(value) -> str:
    return " ".join(str(value).split())


# Checks -----------------------------------------------------------------------------------


def _config(facts):
    config = load_config(facts.args.config)
    return Check("config", "ok", f"loaded {facts.args.config}"), replace(facts, config=config)


def _prompts(facts):
    prompts = load_current_prompts(facts.config)
    return Check("prompts", "ok", ", ".join(prompts)), replace(facts, prompts=prompts)


def _cases(facts):
    cases = project_cases(facts.config, allow_none=True)
    share = sum(case.source == "synthetic" for case in cases) / (len(cases) or 1)
    summary = f"{len(cases)} cases, {share:.0%} synthetic"
    minimum = facts.config.run.min_pairs
    warning = next(
        (message(minimum) for applies, message in CASE_WARNINGS if applies(len(cases), minimum)),
        None,
    )
    check = (
        Check("cases", "ok", summary)
        if warning is None
        else Check("cases", "warn", f"{summary}; {warning}")
    )
    return check, replace(facts, cases=cases)


def _agent(facts):
    config = facts.config
    case_input = facts.cases[0].input if facts.cases else config.agent.example_input
    with project_context(config.root):
        facts = replace(
            facts, input=case_input, agent=project_agent(config), redact=project_redactor(config)
        )
    started = monotonic()
    output = _run(facts, facts.prompts)
    elapsed = monotonic() - started
    preview = _one_line(json.dumps(_redacted(facts, output), ensure_ascii=False))
    preview = preview[:117] + "..." if len(preview) > 120 else preview
    name = config.agent.entry or config.agent.cmd
    check = Check("agent", "ok", f"{name} ran in {elapsed:.3f}s; output: {preview}")
    return check, replace(facts, output=output)


def _prompt_use(facts):
    probed = dict.fromkeys(facts.prompts, PROBE)
    return fallback(
        lambda: (_prompt_check(_run(facts, probed) == facts.output), facts),
        lambda error: (Check("prompt-use", "warn", f"{PROBE_FAILED}: {_one_line(error)}"), facts),
        AgentError,
    )


def _prompt_check(unchanged: bool) -> Check:
    if unchanged:
        return Check("prompt-use", "warn", PROMPT_WARNING)
    return Check("prompt-use", "ok", "the output changed with the supplied prompts")


def _score(facts):
    config = facts.config
    if config.scoring.mode != "score":
        return Check("score", "skip", "judge mode uses scoring.criteria"), facts
    with project_context(config.root):
        score = load_function(config.scoring.score)
        with errors_as(AgentError, {Exception: lambda error: f"scoring.score failed: {error}"}):
            value = score(deepcopy(facts.input), deepcopy(facts.output))
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("expected a finite number")
    return Check("score", "ok", f"{config.scoring.score} returned {value}"), facts


def _llm(facts):
    config = facts.config
    if facts.args.no_llm:
        return Check("llm", "skip", "skipped by --no-llm"), facts
    if config.llm is None:
        return Check("llm", "skip", "score mode has no [llm] table"), facts
    key_status = _key_status(config.llm.api_key_env)
    criteria = (
        config.scoring.criteria
        if config.scoring.mode == "judge"
        else "The output answers the input."
    )
    case_input, output = _redacted(facts, facts.input), _redacted(facts, facts.output)
    result = grade(_judge(facts), case_input, output, criteria)
    message = f"{config.llm.model}; {key_status}; passed={result.passed}: {result.reason}"
    return Check("llm", "ok", message), facts


def _budget(facts):
    config, count = facts.config, len(facts.cases)
    totals = (
        ("gate", sum(planned_calls(count, config.run.samples, config.scoring.mode).values())),
        ("check-up", sum(planned_checkup_calls(config, count, "checkup").values())),
    )
    warnings = [
        f"a full {name} needs {total} calls but max_calls = {config.run.max_calls}; "
        "the verdict would stop at not sure yet"
        for name, total in totals
        if total > config.run.max_calls
    ]
    if warnings:
        return Check("budget", "warn", "; ".join(warnings)), facts
    summary = "; ".join(f"{name}: {total} calls" for name, total in totals)
    return Check("budget", "ok", summary), facts


# Helpers ----------------------------------------------------------------------------------


def _run(facts, prompts) -> Any:
    # Copies on the way in and out: an agent that mutates its arguments or returns shared
    # state must not change what the second call or later checks see.
    with project_context(facts.config.root):
        output = call_agent(
            facts.agent,
            deepcopy(facts.input),
            deepcopy(prompts),
            timeout_s=facts.config.agent.timeout_s,
        )
    return deepcopy(output)


def _redacted(facts, value) -> Any:
    if facts.redact is None:
        return value
    with (
        project_context(facts.config.root),
        errors_as(AgentError, {Exception: lambda error: f"run.redact failed: {error}"}),
    ):
        redacted = facts.redact(deepcopy(value))
        json.dumps(redacted)  # the redacted value is shown and sent, so it must be JSON
    return redacted


def _key_status(name) -> str:
    if not name:
        return "no key variable required"
    if name not in os.environ:
        raise LLMError(f"{name} is unset; set {name} in this shell")
    return f"{name} is set"  # never the value


def _judge(facts) -> Callable[[str], str]:
    for applies, message in LLM_RULES:
        if not applies(facts.config.llm):
            raise ConfigError(message)
    return facts.llm if facts.llm is not None else LLM(**asdict(facts.config.llm))


BLOCKING = ("fail", "skip")
NEEDS_CONFIG = (("config", "needs a valid config"),)
NEEDS_INPUTS = NEEDS_CONFIG + (
    ("prompts", "needs readable prompts"),
    ("cases", "needs readable cases"),
)
NEEDS_AGENT = NEEDS_CONFIG + (("agent", "needs a working agent"),)
CHECKS = (  # config runs first, before the cost line, so it is not listed here
    ("prompts", NEEDS_CONFIG, _prompts),
    ("cases", NEEDS_CONFIG, _cases),
    ("agent", NEEDS_INPUTS, _agent),
    ("prompt-use", NEEDS_AGENT, _prompt_use),
    ("score", NEEDS_AGENT, _score),
    ("llm", NEEDS_AGENT, _llm),
    ("budget", NEEDS_CONFIG + (("cases", "needs readable cases"),), _budget),
)
