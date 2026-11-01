"""Shared paired evaluation: replay, score, decide, and conservatively downgrade."""

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from statistics import fmean
from typing import Protocol

from promptcheckup.data import Case
from promptcheckup.errors import errors_as, fallback
from promptcheckup.llm import SelfCheck, judge_pairs, paired_scores, pairs_from_replay, self_check
from promptcheckup.runner import AgentError, Replay, Run, prompt_hash, replay
from promptcheckup.stats import Verdict, decide

DOWNGRADES = (
    (
        lambda result: result.verdict.decision == "better" and bool(result.errors),
        lambda result: (
            f"{len(result.errors)} of {len(result.replay.runs)} runs failed; "
            "fix the errors and run again"
        ),
    ),
    (
        lambda result: result.budget_exhausted,
        lambda result: "call budget reached; raise run.max_calls or use fewer cases",
    ),
    (
        lambda result: (
            result.scores.self_check is not None and not result.scores.self_check.reliable
        ),
        lambda result: f"judge unreliable: {result.scores.self_check.reason}",
    ),
)
JUDGE_CALLS = {"score": lambda n, s: 0, "judge": lambda n, s: 2 * n * (s + 1)}


@dataclass(frozen=True)
class Scores:
    per_case: list[tuple[str, float]]
    self_check: SelfCheck | None
    skew_guard: bool
    judge_calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    model: str = ""
    budget_exhausted: bool = False
    order_agreement: float | None = None


class Scorer(Protocol):
    """Turn a replay into per-case differences, spending at most `budget` further calls."""

    def __call__(
        self, cases: Sequence[Case], replay: Replay, *, budget: int, min_pairs: int
    ) -> Scores: ...


@dataclass(frozen=True)
class Evaluation:
    verdict: Verdict
    scores: Scores
    replay: Replay
    errors: list[Run]
    budget_exhausted: bool


def score_mode(score_fn) -> Scorer:
    """Average B-minus-A scores only over complete, successful sample pairs."""

    def score(cases, replay, *, budget, min_pairs):
        items = pairs_from_replay(cases, replay)
        differences = [(key, _difference(score_fn, data, a, b)) for key, _, data, a, b in items]
        return Scores(_per_case(differences), None, True)

    return score


def _difference(score_fn, case_input, a, b) -> float:
    with errors_as(AgentError, {Exception: lambda error: f"scoring.score failed: {error}"}):
        difference = float(score_fn(case_input, b)) - float(score_fn(case_input, a))
        if not math.isfinite(difference):
            raise ValueError("paired differences must be finite numbers")
        return difference


def _per_case(differences) -> list[tuple[str, float]]:
    grouped = {}
    for key, difference in differences:
        grouped.setdefault(key, []).append(difference)
    return [(key, fmean(values)) for key, values in grouped.items()]


def judge_mode(llm, criteria, redact=None, *, cache=None) -> Scorer:
    """Use swapped comparisons and a self-check, optionally cached for a named model."""

    def score(cases, replay, *, budget, min_pairs):
        safe_cases, safe_replay = _redacted(cases, replay, redact)
        before = _tokens(llm)
        scores = _judged(llm, criteria, cache, safe_cases, safe_replay, budget, min_pairs)
        return replace(
            scores, model=getattr(llm, "model", ""), **_token_delta(before, _tokens(llm))
        )

    return score


def redacted_inputs(scorer: Scorer, redact) -> Scorer:
    """Redact case inputs before scoring; agent outputs are redacted when they are produced."""
    if redact is None:
        return scorer

    def score(cases, replay, *, budget, min_pairs):
        with errors_as(AgentError, {Exception: lambda error: f"run.redact failed: {error}"}):
            safe = [replace(case, input=redact(case.input)) for case in cases]
        return scorer(safe, replay, budget=budget, min_pairs=min_pairs)

    return score


def _judged(llm, criteria, cache, cases, replay, budget, min_pairs) -> Scores:
    items = pairs_from_replay(cases, replay)
    limited = items[: budget // 2]  # each pair is judged in both orders
    comparisons = judge_pairs(llm, limited, criteria)
    ids = list(dict.fromkeys(item.case_id for item in comparisons))
    values = list(zip(ids, paired_scores(comparisons), strict=True))
    remaining = budget - 2 * len(limited)
    check = _judge_self_check(llm, criteria, cache, cases, replay, remaining, min_pairs)
    return Scores(
        values,
        check,
        False,
        judge_calls=2 * len(limited) + check.calls,
        budget_exhausted=len(limited) < len(items) or check.budget_exhausted,
        order_agreement=(
            sum(item.consistent for item in comparisons) / len(comparisons) if comparisons else None
        ),
    )


def _judge_self_check(llm, criteria, cache, cases, replay, remaining, min_pairs):
    key = _check_key(llm, criteria, cases, replay, min_pairs)
    cached = _read_check(cache, key)
    if cached is not None:
        return replace(cached, calls=0)
    check = self_check(llm, cases, replay, criteria, min_pairs=min_pairs, max_calls=remaining)
    if cache is not None and key and not check.budget_exhausted:
        cache.put(key, "judge-self-check", 0, asdict(check))
    return check


def _check_key(llm, criteria, cases, replay, min_pairs):
    # The verdict depends on who judges, against what, and the A outputs judged.
    judge = {"model": getattr(llm, "model", ""), "base_url": getattr(llm, "base_url", "")}
    if not judge["model"]:
        return ""  # an unnamed judge cannot be recognised next time
    evidence = {
        **judge,
        "criteria": criteria,
        "min_pairs": min_pairs,
        "cases": [(case.id, case.input) for case in cases],
        "runs": [
            (run.case_id, run.sample, run.output, run.error)
            for run in replay.runs
            if run.prompt_set == "A"
        ],
    }
    return prompt_hash({"self-check": json.dumps(evidence, sort_keys=True)})


def _read_check(cache, key):
    if cache is None or not key:
        return None
    found, payload = cache.get(key, "judge-self-check", 0)
    if not found:
        return None
    return fallback(
        lambda: _decoded_check(payload), lambda error: None, KeyError, TypeError, ValueError
    )


def _decoded_check(payload):
    data = dict(payload)
    verdict = data.pop("verdict")
    check = SelfCheck(verdict=Verdict(**verdict) if verdict is not None else None, **data)
    if type(check.reliable) is not bool or not 0 <= check.consistency <= 1:
        raise ValueError("damaged self-check cache")
    if check.reliable and check.verdict is None:
        raise ValueError("reliable self-check needs a verdict")
    return check


def _tokens(llm) -> dict:
    usage = getattr(llm, "usage", None)
    return {name: getattr(usage, name, None) for name in ("prompt_tokens", "completion_tokens")}


def _token_delta(before, after) -> dict:
    return {name: None if before[name] is None else after[name] - before[name] for name in before}


def _redacted(cases, replay, redact):
    if redact is None:
        return cases, replay
    with errors_as(AgentError, {Exception: lambda error: f"run.redact failed: {error}"}):
        safe_cases = [replace(case, input=redact(case.input)) for case in cases]
        runs = [replace(run, output=redact(run.output)) for run in replay.runs if run.error is None]
    return safe_cases, replace(replay, runs=runs)


def evaluate(
    agent,
    cases,
    prompts_a,
    prompts_b,
    scorer,
    *,
    samples,
    timeout_s,
    max_calls,
    min_pairs,
    cache,
    confidence=0.95,
) -> Evaluation:
    """Evaluate paired evidence, preserving statistics even when a safeguard blocks it."""
    replayed = replay(
        agent,
        cases,
        prompts_a,
        prompts_b,
        samples=samples,
        timeout_s=timeout_s,
        max_calls=max_calls,
        cache=cache,
    )
    return evaluate_replay(
        cases,
        replayed,
        scorer,
        budget=max_calls - replayed.calls,
        min_pairs=min_pairs,
        confidence=confidence,
    )


def evaluate_replay(cases, replayed, scorer, *, budget, min_pairs, confidence=0.95) -> Evaluation:
    """Score existing evidence using the same decision and downgrade rules as evaluate."""
    scores = scorer(cases, replayed, budget=budget, min_pairs=min_pairs)
    verdict = decide(
        [value for _, value in scores.per_case],
        min_pairs=min_pairs,
        skew_guard=scores.skew_guard,
        confidence=confidence,
    )
    result = Evaluation(
        verdict,
        scores,
        replayed,
        [run for run in replayed.runs if run.error is not None],
        replayed.budget_exhausted or scores.budget_exhausted,
    )
    return _downgraded(result)


def _downgraded(result: Evaluation) -> Evaluation:
    for predicate, reason in DOWNGRADES:
        if predicate(result):
            verdict = replace(result.verdict, decision="inconclusive", reason=reason(result))
            return replace(result, verdict=verdict)
    return result


def planned_calls(n_cases, samples, mode) -> dict:
    """Cold-cache agent calls and judge calls, including swaps and the A-vs-A check."""
    return {"agent": 2 * n_cases * samples, "judge": JUDGE_CALLS[mode](n_cases, samples)}
