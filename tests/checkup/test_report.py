import re
from dataclasses import replace

import pytest

from promptcheckup.checkup.evaluate import Evaluation, Scores
from promptcheckup.checkup.report import gate_report
from promptcheckup.llm import SelfCheck
from promptcheckup.runner import Replay, Run
from promptcheckup.stats import decide


def evaluation(values):
    scores = Scores([(f"c{i}", value) for i, value in enumerate(values)], None, True)
    runs = [
        Run(f"c{i}", side, 0, f"{side} output {i}")
        for i in range(len(values))
        for side in ("A", "B")
    ]
    return Evaluation(
        decide(values, min_pairs=5), scores, Replay(runs, len(runs), False), [], False
    )


def report(result):
    return gate_report(result, prompt_hashes={"A": "old-hash", "B": "new-hash"}, version="1.2.3")


@pytest.mark.parametrize(
    ("values", "headline"),
    [
        ([2] * 5, "B is better: 5 of 5 cases improved (95% CI +2.00 to +2.00)."),
        ([-2] * 5, "B is worse: 5 of 5 cases regressed (95% CI -2.00 to -2.00)."),
        (
            [0] * 5,
            "Not sure yet: the confidence interval includes zero. Add more cases.",
        ),
    ],
)
def test_report_first_line_is_plain_headline(values, headline):
    assert report(evaluation(values)).splitlines()[0] == headline


def test_small_scores_keep_enough_decimals_to_be_read():
    text = report(evaluation([0.0010, 0.0012, 0.0011, 0.0013, 0.0012]))
    headline, table = text.splitlines()[0], text.split("\n\n")[1]
    numbers = [float(n) for n in re.findall(r"[+-]\d+\.\d+", headline + table)]

    assert headline.startswith("B is better: 5 of 5 cases improved (95% CI +0.00")
    assert numbers and all(number > 0 for number in numbers)


@pytest.mark.parametrize(
    "reason",
    [
        "call budget reached; raise run.max_calls or use fewer cases",
        "judge unreliable: try sharper criteria or a stronger judge model",
    ],
)
def test_downgraded_headline_says_why(reason):
    result = evaluation([2] * 5)
    result = replace(
        result, verdict=replace(result.verdict, decision="inconclusive", reason=reason)
    )

    assert report(result).splitlines()[0] == f"Not sure yet: {reason}."


def test_top_three_cases_are_ordered_by_absolute_difference():
    text = report(evaluation([1, -8, 4, 9, 0]))

    assert text.index("c3") < text.index("c1") < text.index("c2")
    assert "A output 3" in text and "B output 3" in text
    assert "A output 0" not in text


def test_outputs_are_truncated_and_markdown_is_escaped():
    result = evaluation([2] * 5)
    output = "<script>|" + "x" * 500
    runs = [replace(run, output=output) for run in result.replay.runs]
    text = report(replace(result, replay=replace(result.replay, runs=runs)))

    assert "x" * 291 in text
    assert "x" * 292 not in text
    assert "…" in text
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert r"\|" in text


def test_quotes_in_outputs_stay_readable():
    result = evaluation([2] * 5)
    runs = [replace(run, output={"answer": 'it\'s "fine"'}) for run in result.replay.runs]
    text = report(replace(result, replay=replace(result.replay, runs=runs)))

    assert '{"answer": "it\'s \\"fine\\""}' in text
    assert "&quot;" not in text and "&#x27;" not in text


def test_report_contains_self_check_failures_usage_hashes_and_version_in_order():
    result = evaluation([2] * 5)
    error = Run("failed", "B", 1, error="could not answer")
    check = SelfCheck(False, 0.2, None, "too inconsistent")
    result = replace(result, errors=[error], scores=replace(result.scores, self_check=check))
    text = report(result)

    assert text.index("| n |") < text.index("Self-check") < text.index("c0")
    assert text.index("c0") < text.index("Failed runs") < text.index("Calls")
    assert "failed" in text and "could not answer" in text
    assert "20.0%" in text and "too inconsistent" in text
    assert "unreliable for these criteria" in text
    assert "sharper criteria or a stronger judge model" in text
    assert "Tokens" in text and "unavailable" in text
    assert "old-hash" in text and "new-hash" in text and "1.2.3" in text


def test_empty_evaluation_report_is_readable():
    text = report(evaluation([]))

    assert text.startswith("Not sure yet: need at least 5 cases, got 0.")
    assert "n/a" in text


def test_report_includes_available_judge_usage_and_model():
    result = evaluation([1] * 5)
    scores = replace(
        result.scores, judge_calls=30, prompt_tokens=90, completion_tokens=60, model="local-model"
    )
    text = report(replace(result, scores=scores))

    assert "10 agent, 30 judge" in text
    assert "90 input, 60 output" in text
    assert "local-model" in text
