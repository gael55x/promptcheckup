"""Select current failures without hiding agent or score-function errors."""

import math
from statistics import fmean

from promptcheckup.errors import errors_as
from promptcheckup.llm.grade import grade
from promptcheckup.runner import AgentError


def current_failures(cases, replay, *, score_fn, llm, criteria):
    """Grade sample zero, or rank mean case scores; always include errored cases."""
    inputs = {case.id: case.input for case in cases}
    errors = _errors(replay.runs, inputs)
    runs = [run for run in replay.runs if run.error is None]
    selected = (
        _scored(runs, inputs, score_fn)
        if score_fn is not None
        else _graded(runs, inputs, llm, criteria)
    )
    return list({**{row[0]: row for row in selected}, **errors}.values())


def _errors(runs, inputs):
    errors = {}
    for run in runs:
        if run.error is not None:
            errors.setdefault(
                run.case_id, (run.case_id, inputs[run.case_id], run.output, run.error)
            )
    return errors


def _graded(runs, inputs, llm, criteria):
    failures = []
    for run in runs:
        if run.sample != 0:
            continue
        result = grade(llm, inputs[run.case_id], run.output, criteria)
        if not result.passed:
            failures.append((run.case_id, inputs[run.case_id], run.output, result.reason))
    return failures


def _scored(runs, inputs, score_fn):
    grouped = {}
    for run in runs:
        grouped.setdefault(run.case_id, []).append(run)
    scores = [
        (key, _mean_score(samples, inputs[key], score_fn)) for key, samples in grouped.items()
    ]
    # Scores have no known maximum, so the bottom 20% are always candidates. A week with
    # nothing to fix costs one proposal call; the gate rejects an edit that changes nothing.
    bottom = sorted(scores, key=lambda item: item[1])[: max(1, math.ceil(len(scores) / 5))]
    return [
        (key, inputs[key], grouped[key][0].output, f"low score {value:g}") for key, value in bottom
    ]


def _mean_score(runs, case_input, score_fn):
    with errors_as(AgentError, {Exception: lambda error: f"scoring.score failed: {error}"}):
        scores = [float(score_fn(case_input, run.output)) for run in runs]
        if not all(math.isfinite(score) for score in scores):
            raise ValueError("scores must be finite numbers")
        return fmean(scores)
