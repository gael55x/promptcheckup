"""Small, offline experiments through the shipped check-up, never a statistical replica."""

import csv
import importlib
import json
import math
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

BENCHMARKS = Path(__file__).resolve().parents[2] / "benchmarks"


@pytest.fixture
def simulation(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCHMARKS))
    return importlib.import_module("run_checkup_simulation")


@pytest.fixture
def tiny(simulation):
    incidents = tuple(
        replace(incident, week=week) for week, incident in enumerate(simulation.INCIDENTS, start=2)
    )
    return simulation.Config(
        weeks=6, repeats=2, cases=20, provider_effect_sd=3, fix_effect_sd=3, incidents=incidents
    )


def test_deterministic_csv_rows(simulation, tiny, tmp_path):
    first = simulation.simulation_rows(tiny, tmp_path / "first")
    second = simulation.simulation_rows(tiny, tmp_path / "second")
    assert first == second
    paths = [tmp_path / "a.csv", tmp_path / "b.csv"]
    for path, rows in zip(paths, (first, second), strict=True):
        simulation.write_csv(path, rows)
    assert paths[0].read_bytes() == paths[1].read_bytes()
    with paths[0].open() as stream:
        assert len(list(csv.DictReader(stream))) == 12


def test_real_checkup_snapshots_and_stub_call_counts(simulation, tiny, tmp_path):
    rows = simulation.simulation_rows(tiny, tmp_path)
    snapshots = list(tmp_path.rglob(".promptcheckup/snapshots/*.json"))
    assert len(snapshots) >= 6  # original, kept fix and unreviewed version, per repeat
    assert all(len(json.loads(path.read_text())["runs"]) == 40 for path in snapshots)
    assert all(row["calls"] == row["agent_calls"] + row["llm_calls"] for row in rows)
    assert all(row["llm_calls"] == 2 for row in rows)
    quiet = next(row for row in rows if row["incident"] == "quiet")
    real = next(row for row in rows if row["incident"] == "real_fix")
    assert quiet["calls"] == 42  # 40 samples, patterns, unchanged proposal
    assert real["calls"] == 122  # another 80 calls for the real paired replay
    assert quiet["drift_decision"] == "no_baseline"


def test_large_harmful_provider_change_flags_in_incident_week(simulation, tiny, tmp_path):
    rows = simulation.simulation_rows(tiny, tmp_path)
    incident = [row for row in rows if row["incident"] == "harmful_provider"]
    assert sum(row["drift_decision"] == "worse" for row in incident) / len(incident) >= 0.8


def test_harmless_provider_false_alarms_stay_within_sampling_budget(simulation, tiny, tmp_path):
    # Three weeks suffice; more repeats avoid asserting zero false positives from two draws.
    config = replace(tiny, weeks=3, repeats=200, provider_effect_sd=0)
    rows = simulation.simulation_rows(config, tmp_path)
    incident = [row for row in rows if row["incident"] == "harmless_provider"]
    flagged = sum(row["drift_decision"] == "worse" for row in incident) / len(incident)
    target = 0.025  # one side of the library's 95% interval, plus three sampling SEs
    assert flagged <= target + 3 * math.sqrt(target * (1 - target) / config.repeats)


def test_real_fix_kept_useless_fix_rejected_by_gate(simulation, tiny, tmp_path):
    rows = simulation.simulation_rows(tiny, tmp_path)
    real = [row for row in rows if row["incident"] == "real_fix"]
    useless = [row for row in rows if row["incident"] == "useless_fix"]
    assert all(row["proposal_outcome"] == "kept" for row in real)
    assert all(row["proposal_decision"] == "better" for row in real)
    assert all(row["proposal_outcome"] == "rejected" for row in useless)
    assert all(row["proposal_decision"] == "inconclusive" for row in useless)
    assert all(row["prompt_version"] == 2 for row in useless)  # merged the following week
    assert all(row["drift_decision"] == "better" for row in useless)


def test_unreviewed_merge_is_detected_in_incident_week(simulation, tiny, tmp_path):
    config = replace(tiny, repeats=10, regression_effect_sd=3)
    rows = simulation.simulation_rows(config, tmp_path)
    incident = [row for row in rows if row["incident"] == "unreviewed_regression"]
    assert all(row["prompt_version"] == 0 for row in incident)
    assert sum(row["drift_decision"] == "worse" for row in incident) / len(incident) >= 0.8
    assert simulation.metrics(rows)["unreviewed_regressions_noticed"] >= 0.8


def test_agent_depends_on_prompt_and_week_with_reproducible_samples(simulation, tiny):
    case = simulation.make_cases(tiny)[0].input
    week = simulation.Week(repeat=0, week=1, environment=0, provider="original")
    calls = []
    run = simulation.make_agent(tiny, week, calls)
    baseline = run(case, simulation.prompts(1))
    again = simulation.make_agent(tiny, week, [])(case, simulation.prompts(1))
    improved = simulation.make_agent(tiny, week, [])(case, simulation.prompts(2))
    next_week = simulation.make_agent(tiny, replace(week, week=2), [])
    assert baseline == again
    assert improved["quality"] - baseline["quality"] == pytest.approx(3)
    assert run(case, simulation.prompts(1)) != baseline  # sample counter advances
    assert next_week(case, simulation.prompts(1)) != baseline
    assert len(calls) == 2
    assert simulation.score(case, baseline) == baseline["quality"] - case["difficulty"]


def test_harmless_provider_changes_only_unscored_output(simulation, tiny):
    case = simulation.make_cases(tiny)[0].input
    week = simulation.Week(0, 1, 0, "original")
    first = simulation.make_agent(tiny, week, [])(case, simulation.prompts(1))
    second = simulation.make_agent(tiny, replace(week, provider="reworded"), [])(
        case, simulation.prompts(1)
    )
    assert first != second
    assert simulation.score(case, first) == simulation.score(case, second)


def row(repeat, week, incident="quiet", drift="inconclusive", proposal="not_proposed"):
    return {
        "repeat": repeat,
        "week": week,
        "incident": incident,
        "drift_decision": drift,
        "proposal_outcome": proposal,
    }


def test_metrics_on_hand_built_rows(simulation):
    rows = [
        row(0, 1, "harmful_provider"),
        row(0, 2, drift="worse"),
        row(0, 3),
        row(0, 4, "harmless_provider"),
        row(0, 5, "real_fix", proposal="kept"),
        row(0, 6, "useless_fix", proposal="rejected"),
        row(0, 7, "unreviewed_regression", "no_baseline"),
        row(0, 8),
        row(0, 9),
        row(1, 1, "harmful_provider", "worse"),
        row(1, 2),
        row(1, 3),
        row(1, 4, "harmless_provider", "worse"),
        row(1, 5, "real_fix", proposal="rejected"),
        row(1, 6, "useless_fix", proposal="kept"),
        row(1, 7, "unreviewed_regression", "no_baseline"),
        row(1, 8, drift="worse"),
        row(1, 9),
    ]
    result = simulation.metrics(rows)
    rates = {item["incident"]: item for item in result["incidents"]}
    assert rates["harmful_provider"]["same_week"] == 0.5
    assert rates["harmful_provider"]["within_two_weeks"] == 1
    assert rates["harmless_provider"]["same_week"] == 0.5
    assert rates["unreviewed_regression"]["within_two_weeks"] == 0.5
    assert result["quiet_false_alarm_rate"] == 2 / 8
    assert result["false_alarms_per_year"] == 1  # measured per repeat, not rate * 52
    assert result["real_fixes_kept"] == result["useless_fixes_rejected"] == 0.5
    assert result["unreviewed_regressions_noticed"] == 0


def test_detection_window_is_inclusive_and_never_crosses_repeats(simulation):
    rows = [
        row(0, 1, "harmful_provider"),
        row(0, 2),
        row(0, 3, drift="worse"),
        row(0, 4, drift="worse"),
        row(1, 1, "harmful_provider"),
        row(1, 2),
        row(1, 3),
        row(1, 4, drift="worse"),
    ]
    rates = simulation.metrics(rows)["incidents"][0]
    assert rates["same_week"] == 0
    assert rates["within_two_weeks"] == 0.5


def test_chart_reads_csv_and_contains_all_five_incident_markers(simulation, tiny, tmp_path):
    charts = importlib.import_module("charts")
    rows = simulation.simulation_rows(tiny, tmp_path / "runs")
    csv_path, svg_path = tmp_path / "checkup.csv", tmp_path / "timeline.svg"
    simulation.write_csv(csv_path, rows)
    svg_path.write_text(charts.checkup_timeline_chart(csv_path))
    markup = svg_path.read_text()
    root = ElementTree.fromstring(markup)
    assert root.attrib["role"] == "img"
    assert charts.STYLE in markup
    assert all(f'id="incident-{incident.kind}"' in markup for incident in tiny.incidents)
    assert 'id="proposal-kept"' in markup
    assert 'id="proposal-rejected"' in markup
    assert len(root.findall(".//{*}rect[@class='s1']")) == tiny.weeks


def test_null_check_gates_a_useless_edit_many_times_through_the_real_evaluation(simulation):
    counts = simulation.useless_fix_null_check(simulation.Config(), trials=12)

    assert set(counts) == {"better", "worse", "inconclusive"}
    assert sum(counts.values()) == 12
    assert counts == simulation.useless_fix_null_check(simulation.Config(), trials=12)  # seeded
