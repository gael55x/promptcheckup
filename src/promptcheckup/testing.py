"""Test-suite helpers using the shipped gate, with no test-runner dependency."""

import random
from copy import deepcopy

from promptcheckup import __version__
from promptcheckup.checkup.evaluate import Evaluation, evaluate, judge_mode, score_mode
from promptcheckup.checkup.report import gate_report
from promptcheckup.data import Case, case_id
from promptcheckup.errors import errors_as
from promptcheckup.runner import function_agent

SCORING_RULES = (
    (
        lambda score, judge: (score is None) != (judge is None),
        "provide exactly one of score or judge",
    ),
)


def gate(
    agent,
    cases,
    old,
    new,
    *,
    score=None,
    judge=None,
    criteria="",
    samples=2,
    min_pairs=20,
    confidence=0.95,
    max_calls=400,
) -> Evaluation:
    """Compare prompts on Case objects or input dicts, using exactly one scorer."""
    scorer = _scorer(score, judge, criteria)
    return evaluate(
        function_agent(agent),
        _cases(cases),
        old,
        new,
        scorer,
        samples=samples,
        timeout_s=60,
        max_calls=max_calls,
        min_pairs=min_pairs,
        cache=None,
        confidence=confidence,
    )


def _cases(cases):
    return [Case(case_id(case), case) if isinstance(case, dict) else case for case in cases]


def _scorer(score, judge, criteria):
    for predicate, message in SCORING_RULES:
        if not predicate(score, judge):
            raise ValueError(message)
    return score_mode(score) if score is not None else judge_mode(judge, criteria)


def assert_better(agent, cases, old, new, **settings) -> Evaluation:
    """Accept gate's arguments and require a 'better' verdict, returning its evidence."""
    result = gate(agent, cases, old, new, **settings)
    return _assert_verdict(result, ("better",), settings.get("confidence", 0.95))


def assert_not_worse(agent, cases, old, new, **settings) -> Evaluation:
    """Accept gate's arguments and pass unless 'worse'; inconclusive also passes."""
    result = gate(agent, cases, old, new, **settings)
    return _assert_verdict(result, ("better", "inconclusive"), settings.get("confidence", 0.95))


def _assert_verdict(result, allowed, confidence) -> Evaluation:
    if result.verdict.decision not in allowed:
        report = gate_report(result, prompt_hashes={}, version=__version__, confidence=confidence)
        headline, table = report.split("\n\n")[:2]  # the report's first two sections
        raise AssertionError(headline + "\n\n" + table)
    return result


def assert_no_lookahead(view, history, decision_times, *, time_key="time") -> None:
    """Require a view to ignore affine/random future changes and removal of future rows."""
    for time in decision_times:
        _assert_time(view, history, time, time_key)


def _assert_time(view, history, time, time_key):
    expected = deepcopy(view(deepcopy(history), time))
    message = f"view({time!r}) changed when only data after {time!r} changed: it reads the future"
    for changed in _future_histories(history, time, time_key):
        with errors_as(AssertionError, {Exception: message}):
            actual = view(deepcopy(changed), time)
        if actual != expected:
            raise AssertionError(message)


def _future_histories(history, time, time_key):
    rng = random.Random(0)
    changes = (lambda value: value * 1.37 + 1, lambda value: value * rng.uniform(0.5, 1.5))
    for change in changes:
        yield [_perturbed_row(row, time, time_key, change) for row in history]
    yield [row for row in history if row[time_key] <= time]


def _perturbed_row(row, time, time_key, change):
    if row[time_key] <= time:
        return dict(row)
    return {
        key: value if key == time_key else _perturbed_number(value, change)
        for key, value in row.items()
    }


def _perturbed_number(value, change):
    return (
        change(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
    )
