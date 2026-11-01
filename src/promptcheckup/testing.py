"""Test-suite helpers using the shipped gate, with no test-runner dependency."""

from promptcheckup import __version__
from promptcheckup.checkup.evaluate import Evaluation, evaluate, judge_mode, score_mode
from promptcheckup.checkup.report import gate_report
from promptcheckup.data import Case, case_id
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
    """Require a row-based view to ignore changes to numeric columns after each time."""
    for time in decision_times:
        perturbed = [_perturbed_row(row, time, time_key) for row in history]
        if view(perturbed, time) != view(history, time):
            raise AssertionError(
                f"view({time!r}) changed when only data after {time!r} changed: it reads the future"
            )


def _perturbed_row(row, time, time_key):
    if row[time_key] <= time:
        return dict(row)
    return {
        key: value if key == time_key else _perturbed_number(value) for key, value in row.items()
    }


def _perturbed_number(value):
    return (
        value * 1.37 + 1
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else value
    )
