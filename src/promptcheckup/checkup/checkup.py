"""Weekly check-up and proposal pipelines: named steps over one frozen state and budget.

Each step takes the fixed plan and the state so far and returns a new state. A step
that cannot afford its calls stops the pipeline; the result then says the budget ran
out, keeps the evidence gathered so far, and never keeps a proposal.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import reduce
from pathlib import Path
from typing import Any

from promptcheckup.checkup.evaluate import (
    Evaluation,
    Scorer,
    evaluate,
    evaluate_replay,
    judge_mode,
    redacted_inputs,
    score_mode,
)
from promptcheckup.checkup.failures import current_failures
from promptcheckup.checkup.snapshot import load_snapshot, save_snapshot
from promptcheckup.data import Case, append_cases
from promptcheckup.errors import errors_as, fallback
from promptcheckup.llm.patterns import Pattern, failure_patterns
from promptcheckup.llm.propose import Proposal, ProposalRejected, propose
from promptcheckup.llm.starter import starter_cases
from promptcheckup.runner import Agent, AgentError, Cache, Replay, Run, prompt_hash, run_prompts
from promptcheckup.stats import Verdict


@dataclass(frozen=True)
class CheckupResult:
    starter_created: int
    drift: Evaluation | None
    agent_version_changed: bool
    patterns: list[Pattern]
    proposal: Proposal | None
    proposal_verdict: Verdict | None
    proposal_rejection: str | None
    calls: int
    synthetic_share: float = 0.0
    budget_exhausted: bool = False
    errors: list[Run] = field(default_factory=list)
    prompts_hash: str = ""
    model: str = ""
    drift_checked: bool = True
    snapshot_saved: bool = False


@dataclass(frozen=True)
class _Plan:
    """Everything fixed for one run. `score_fn` selects score mode; otherwise judge mode."""

    agent: Agent
    prompts: dict[str, str]
    llm: Callable[[str], str]
    directory: Path
    score_fn: Callable[[Any, Any], float] | None = None
    criteria: str = ""
    scorer: Scorer | None = None
    samples: int = 2
    timeout_s: float = 60
    max_calls: int = 400
    min_pairs: int = 20
    cache: Cache | None = None
    agent_version: str = ""
    description: str = ""
    example_input: dict = field(default_factory=dict)
    redact: Callable[[Any], Any] | None = None


@dataclass(frozen=True)
class _State:
    result: CheckupResult
    cases: list[Case]
    current: Replay = field(default_factory=lambda: Replay([], 0, False))
    failures: list[tuple] = field(default_factory=list)
    stopped: bool = False


def checkup(agent, cases, prompts, llm, **settings) -> CheckupResult:
    """Starter cases, current runs, drift, failures, patterns, and a gated proposal.

    Settings are the fields of `_Plan`: directory (project root), score_fn or criteria,
    samples, timeout_s, max_calls, min_pairs, cache, agent_version, description,
    example_input, redact, and optionally a scorer following evaluate's protocol.
    """
    plan = _plan(agent, prompts, llm, settings)
    return _run(plan, _start(plan, cases), CHECKUP_STEPS)


def propose_only(agent, cases, prompts, llm, **settings) -> CheckupResult:
    """Current runs, failures, patterns and the gate, without starter cases or drift."""
    if not cases:
        raise ValueError("propose requires existing cases")
    plan = _plan(agent, prompts, llm, settings)
    state = _start(plan, cases)
    return _run(plan, replace(state, result=replace(state.result, drift_checked=False)), PROPOSE)


def _plan(agent, prompts, llm, settings) -> _Plan:
    plan = _Plan(agent, prompts, llm, **settings)
    # Outputs are redacted as they leave the agent: before the cache, snapshots and the LLM.
    return replace(
        plan,
        directory=Path(plan.directory),
        agent=_redacting(agent, plan.redact),
        scorer=_scorer(plan),
    )


def _start(plan, cases) -> _State:
    model = getattr(plan.llm, "model", "")
    result = CheckupResult(
        0, None, False, [], None, None, None, 0, prompts_hash=prompt_hash(plan.prompts), model=model
    )
    return _State(result, list(cases))


def _run(plan, state, steps) -> CheckupResult:
    final = reduce(lambda done, step: done if done.stopped else step(plan, done), steps, state)
    if final.stopped:
        return replace(final.result, budget_exhausted=True, proposal=None)
    return final.result


# Steps ---------------------------------------------------------------------------------


def _starter(plan, state) -> _State:
    if state.cases:
        return state
    if _remaining(plan, state) < 1:
        return replace(state, stopped=True)
    example = _redacted(plan.redact, plan.example_input)
    generated = starter_cases(plan.llm, plan.prompts, plan.description, example)
    cases = [replace(case, input=_redacted(plan.redact, case.input)) for case in generated]
    append_cases(plan.directory / "cases" / "starter.jsonl", cases)
    return _spend(replace(state, cases=cases), 1, starter_created=len(cases))


def _current(plan, state) -> _State:
    replayed = run_prompts(
        plan.agent,
        state.cases,
        plan.prompts,
        samples=plan.samples,
        timeout_s=plan.timeout_s,
        max_calls=_remaining(plan, state),
        cache=plan.cache,
    )
    share = sum(case.source == "synthetic" for case in state.cases) / len(state.cases)
    errors = [run for run in replayed.runs if run.error is not None]
    state = _spend(state, replayed.calls, synthetic_share=share, errors=errors)
    return replace(state, current=replayed, stopped=replayed.budget_exhausted)


def _drift(plan, state) -> _State:
    previous = load_snapshot(plan.directory, state.result.prompts_hash)
    if previous is not None:
        state = _compare_with(plan, state, previous)
    if state.stopped:
        return state  # an incomplete comparison keeps the previous snapshot
    save_snapshot(plan.directory, state.result.prompts_hash, state.current.runs, plan.agent_version)
    return _spend(state, 0, snapshot_saved=True)


def _compare_with(plan, state, previous) -> _State:
    ids = {case.id for case in state.cases}
    old = [run for run in previous.runs if run.case_id in ids]
    new = [replace(run, prompt_set="B") for run in state.current.runs]
    drift = evaluate_replay(
        state.cases,
        Replay(old + new, 0, False),
        plan.scorer,
        budget=_remaining(plan, state),
        min_pairs=plan.min_pairs,
    )
    changed = previous.agent_version != plan.agent_version
    state = _spend(state, drift.scores.judge_calls, drift=drift, agent_version_changed=changed)
    return replace(state, stopped=drift.budget_exhausted)


def _failures(plan, state) -> _State:
    judged = plan.score_fn is None
    cost = sum(run.sample == 0 and run.error is None for run in state.current.runs) if judged else 0
    if _remaining(plan, state) < cost:
        return replace(state, stopped=True)
    failures = current_failures(
        _safe_cases(plan, state.cases) if judged else state.cases,
        state.current,
        score_fn=plan.score_fn,
        llm=plan.llm,
        criteria=plan.criteria,
    )
    safe = _safe_failures(plan, state.cases, failures)
    return replace(_spend(state, cost), failures=safe)


def _patterns(plan, state) -> _State:
    cost = 1 if state.failures else 0
    if _remaining(plan, state) < cost:
        return replace(state, stopped=True)
    return _spend(state, cost, patterns=failure_patterns(plan.llm, state.failures))


def _proposal(plan, state) -> _State:
    if not state.failures:
        return state
    if _remaining(plan, state) < 1:
        return replace(state, stopped=True)
    spent = _spend(state, 1)
    # A rejected proposal is a finding for the report, not a failure of the run.
    return fallback(
        lambda: _gate(
            plan, spent, propose(plan.llm, plan.prompts, spent.result.patterns, spent.failures)
        ),
        lambda error: _spend(spent, 0, proposal_rejection=str(error)),
        ProposalRejected,
    )


def _gate(plan, state, proposal) -> _State:
    # Replays current against proposed back-to-back; cached current outputs are free.
    result = evaluate(
        plan.agent,
        state.cases,
        plan.prompts,
        proposal.prompts,
        plan.scorer,
        samples=plan.samples,
        timeout_s=plan.timeout_s,
        max_calls=_remaining(plan, state),
        min_pairs=plan.min_pairs,
        cache=plan.cache,
    )
    proposed_errors = [run for run in result.errors if run.prompt_set == "B"]
    state = _spend(
        state,
        result.replay.calls + result.scores.judge_calls,
        proposal_verdict=result.verdict,
        proposal=proposal if result.verdict.decision == "better" else None,
        errors=[*state.result.errors, *proposed_errors],
    )
    return replace(state, stopped=result.budget_exhausted)


CHECKUP_STEPS = (_starter, _current, _drift, _failures, _patterns, _proposal)
PROPOSE = (_current, _failures, _patterns, _proposal)


# Helpers -------------------------------------------------------------------------------


def _remaining(plan, state) -> int:
    return plan.max_calls - state.result.calls


def _spend(state, calls, **fields) -> _State:
    result = replace(state.result, calls=state.result.calls + calls, **fields)
    return replace(state, result=result)


def _scorer(plan) -> Scorer:
    if plan.scorer is not None:
        return plan.scorer
    if plan.score_fn is not None:
        return score_mode(plan.score_fn)
    return redacted_inputs(judge_mode(plan.llm, plan.criteria, cache=plan.cache), plan.redact)


def _redacting(agent, redact) -> Agent:
    if redact is None:
        return agent
    return lambda case, prompts, timeout_s: _redacted(redact, agent(case, prompts, timeout_s))


def _redacted(redact, value):
    if redact is None:
        return value
    with errors_as(AgentError, {Exception: lambda error: f"run.redact failed: {error}"}):
        return redact(value)


def _safe_cases(plan, cases) -> list[Case]:
    return [replace(case, input=_redacted(plan.redact, case.input)) for case in cases]


def _safe_failures(plan, cases, failures) -> list[tuple]:
    # Inputs and error text are redacted before they reach the pattern and proposal calls.
    inputs = {case.id: _redacted(plan.redact, case.input) for case in cases}
    return [
        (key, inputs[key], output, _redacted(plan.redact, reason))
        for key, _, output, reason in failures
    ]
