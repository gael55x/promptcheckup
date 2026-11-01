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
from promptcheckup.checkup.ledger import record_trial
from promptcheckup.checkup.snapshot import load_latest_snapshot, save_snapshot
from promptcheckup.checkup.split import split_cases, valid_holdout_share
from promptcheckup.data import Case, append_cases
from promptcheckup.errors import fallback
from promptcheckup.llm.patterns import Pattern, failure_patterns
from promptcheckup.llm.propose import Proposal, ProposalRejected, propose
from promptcheckup.llm.starter import starter_cases
from promptcheckup.runner import Agent, Cache, Replay, Run, prompt_hash, run_prompts
from promptcheckup.runner.redaction import (
    redacted_error,
    redacted_run,
    redacted_value,
    redacting_agent,
)
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
    gated_proposal: Proposal | None = None  # audit evidence, never authorization to write files
    drift_baseline: str = "same prompt"
    previous_prompts_hash: str = ""
    current_runs: int = 0
    current_errors: int = 0
    failures: list[tuple] = field(default_factory=list)
    untested_proposal: Proposal | None = None

    @property
    def all_current_failed(self) -> bool:
        return self.current_runs > 0 and self.current_errors == self.current_runs


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
    gate_cases: list[Case] | None = None
    confidence: float = 0.95
    trial_command: str | None = None  # CLI opts into durable recording immediately after gating
    holdout_share: float | None = None


@dataclass(frozen=True)
class _State:
    result: CheckupResult
    cases: list[Case]
    current: Replay = field(default_factory=lambda: Replay([], 0, False))
    failures: list[tuple] = field(default_factory=list)
    stopped: bool = False
    learning_cases: list[Case] = field(default_factory=list)
    gate_cases: list[Case] = field(default_factory=list)


def checkup(
    agent, cases, prompts, llm, *, holdout_share: float | None = None, **settings
) -> CheckupResult:
    """Starter cases, current runs, drift, failures, patterns, and a gated proposal.

    Settings are the fields of `_Plan`: directory (project root), score_fn or criteria,
    samples, timeout_s, max_calls, min_pairs, cache, agent_version, description,
    example_input, redact, and optionally a scorer following evaluate's protocol.
    gate_cases and confidence override only the proposal gate. trial_command opts
    into recording each gate immediately, including rejected and budget-stopped gates.
    """
    plan = _plan(agent, prompts, llm, dict(settings, holdout_share=holdout_share))
    return _run(plan, _start(plan, cases), CHECKUP_STEPS)


def propose_only(
    agent, cases, prompts, llm, *, holdout_share: float | None = None, **settings
) -> CheckupResult:
    """Current runs, failures, patterns and the gate, without starter cases or drift."""
    if not cases:
        raise ValueError("propose requires existing cases")
    plan = _plan(agent, prompts, llm, dict(settings, holdout_share=holdout_share))
    state = _start(plan, cases)
    return _run(plan, replace(state, result=replace(state.result, drift_checked=False)), PROPOSE)


def _plan(agent, prompts, llm, settings) -> _Plan:
    plan = _Plan(agent, prompts, llm, **settings)
    _validate_holdout(plan)
    # Outputs are redacted as they leave the agent: before the cache, snapshots and the LLM.
    return replace(
        plan,
        directory=Path(plan.directory),
        agent=redacting_agent(agent, plan.redact),
        scorer=_scorer(plan),
    )


def _validate_holdout(plan):
    if plan.holdout_share is None:
        return
    if not valid_holdout_share(plan.holdout_share):
        raise ValueError("holdout_share must be between 0 and 1 (exclusive)")
    if plan.gate_cases is not None:
        raise ValueError("provide holdout_share or gate_cases, not both")


def _start(plan, cases) -> _State:
    model = getattr(plan.llm, "model", "")
    result = CheckupResult(
        0, None, False, [], None, None, None, 0, prompts_hash=prompt_hash(plan.prompts), model=model
    )
    return _State(result, list(cases))


def _run(plan, state, steps) -> CheckupResult:
    final = reduce(lambda done, step: done if done.stopped else step(plan, done), steps, state)
    if final.stopped and not final.result.all_current_failed:
        return replace(final.result, budget_exhausted=True, proposal=None)
    return final.result


# Steps ---------------------------------------------------------------------------------


def _starter(plan, state) -> _State:
    if state.cases:
        return state
    if _remaining(plan, state) < 1:
        return replace(state, stopped=True)
    example = redacted_value(plan.redact, plan.example_input)
    generated = starter_cases(plan.llm, plan.prompts, plan.description, example)
    cases = [replace(case, input=redacted_value(plan.redact, case.input)) for case in generated]
    append_cases(plan.directory / "cases" / "starter.jsonl", cases)
    return _spend(replace(state, cases=cases), 1, starter_created=len(cases))


def _split(plan, state) -> _State:
    if plan.holdout_share is None:
        gate = state.cases if plan.gate_cases is None else list(plan.gate_cases)
        return replace(state, learning_cases=state.cases, gate_cases=gate)
    learning, gate = split_cases(state.cases, plan.holdout_share)
    return replace(state, learning_cases=learning, gate_cases=gate)


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
    state = _spend(
        state,
        replayed.calls,
        synthetic_share=share,
        errors=errors,
        current_runs=len(replayed.runs),
        current_errors=len(errors),
    )
    return replace(
        state,
        current=replayed,
        stopped=replayed.budget_exhausted or state.result.all_current_failed,
    )


def _drift(plan, state) -> _State:
    state = _compare_snapshot(plan, state)
    if state.stopped:
        return state  # an incomplete comparison keeps the previous snapshot
    save_snapshot(plan.directory, state.result.prompts_hash, state.current.runs, plan.agent_version)
    return _spend(state, 0, snapshot_saved=True)


def _compare_snapshot(plan, state) -> _State:
    latest = load_latest_snapshot(plan.directory)
    if latest is None:
        return state
    previous_hash, previous = latest
    if previous_hash != state.result.prompts_hash:
        state = _spend(
            state, 0, drift_baseline="previous prompt", previous_prompts_hash=previous_hash
        )
    return _compare_with(plan, state, previous)


def _compare_with(plan, state, previous) -> _State:
    ids = {case.id for case in state.cases}
    old = [redacted_run(plan.redact, run) for run in previous.runs if run.case_id in ids]
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
    learning = _learning_failures(plan, state, safe)
    return replace(_spend(state, cost, failures=safe), failures=learning)


def _learning_failures(plan, state, failures):
    ids = {case.id for case in state.learning_cases}
    if plan.score_fn is None or plan.holdout_share is None:
        return [row for row in failures if row[0] in ids]
    runs = [run for run in state.current.runs if run.case_id in ids]
    selected = current_failures(
        state.learning_cases,
        replace(state.current, runs=runs),
        score_fn=plan.score_fn,
        llm=plan.llm,
        criteria=plan.criteria,
    )
    return _safe_failures(plan, state.learning_cases, selected)


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
    # Replays both sides back-to-back unless the entire pair is cached.
    cases = state.gate_cases
    if plan.holdout_share is not None and len(cases) < plan.min_pairs:
        return _spend(
            state,
            0,
            untested_proposal=proposal,
            proposal_rejection=(
                f"not tested: {len(cases)} held-out cases, need at least "
                f"{plan.min_pairs}; add cases"
            ),
        )
    result = evaluate(
        plan.agent,
        cases,
        plan.prompts,
        proposal.prompts,
        plan.scorer,
        samples=plan.samples,
        timeout_s=plan.timeout_s,
        max_calls=_remaining(plan, state),
        min_pairs=plan.min_pairs,
        cache=plan.cache,
        confidence=plan.confidence,
    )
    _record_trial(plan, cases, proposal, result)
    state = _spend(
        state,
        result.replay.calls + result.scores.judge_calls,
        proposal_verdict=result.verdict,
        gated_proposal=proposal,
        proposal=proposal if result.verdict.decision == "better" else None,
        errors=[*state.result.errors, *result.errors],
    )
    return replace(state, stopped=result.budget_exhausted)


def _record_trial(plan, cases, proposal, result):
    if plan.trial_command is not None:
        record_trial(
            plan.directory,
            case_ids=[case.id for case in cases],
            prompt_a=prompt_hash(plan.prompts),
            prompt_b=prompt_hash(proposal.prompts),
            decision=result.verdict.decision,
            confidence=plan.confidence,
            command=plan.trial_command,
        )


CHECKUP_STEPS = (_starter, _split, _current, _drift, _failures, _patterns, _proposal)
PROPOSE = (_split, _current, _failures, _patterns, _proposal)


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


def _safe_cases(plan, cases) -> list[Case]:
    return [replace(case, input=redacted_value(plan.redact, case.input)) for case in cases]


def _safe_failures(plan, cases, failures) -> list[tuple]:
    # Inputs and error text are redacted before they reach the pattern and proposal calls.
    inputs = {case.id: redacted_value(plan.redact, case.input) for case in cases}
    return [
        (key, inputs[key], output, redacted_error(plan.redact, reason))
        for key, _, output, reason in failures
    ]
