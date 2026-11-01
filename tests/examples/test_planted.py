"""The project's reason to exist: shared noise fools a naive comparison but not paired replay."""

import importlib.util
from pathlib import Path

from promptcheckup import decide

_spec = importlib.util.spec_from_file_location(
    "planted", Path(__file__).parents[2] / "examples" / "planted" / "planted.py"
)
planted = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(planted)


def test_naive_comparison_picks_the_wrong_prompt_in_a_regime_shift():
    weeks = planted.make_weeks(seed=0)

    naive = planted.naive_difference(weeks, edge=0.3, seed=0)

    assert naive < 0  # B looks worse, only because its week had a falling market


def test_paired_replay_finds_the_real_edge():
    weeks = planted.make_weeks(seed=0)

    verdict = decide(planted.paired_differences(weeks.second, edge=0.3, seed=0))

    assert verdict.decision == "better"
    assert verdict.ci_low < 0.3 < verdict.ci_high


def test_paired_replay_stays_inconclusive_when_the_edit_does_nothing():
    weeks = planted.make_weeks(seed=0)

    verdict = decide(planted.paired_differences(weeks.second, edge=0.0, seed=0))

    assert verdict.decision == "inconclusive"
