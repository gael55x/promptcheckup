import json
from dataclasses import FrozenInstanceError, replace

import pytest

from promptcheckup.checkup.evolve import evolve, round_confidence, split_cases
from promptcheckup.checkup.report import evolve_report
from promptcheckup.data import Case
from promptcheckup.runner import function_agent
from promptcheckup.stats import decide
from tests.checkup.test_checkup import block, llm_stub

PROMPTS = {"system": "version: 0\nFollow the rules.\n"}
# Fixed IDs: the 20 lowest SHA-256 hashes form the held-out half at share 0.5.
CASE_OFFSETS = (
    (0, (6, 9, 14, 18, 19, 21, 23, 24, 28, 33, 34, 40, 42, 49, 50, 52, 56, 57, 58, 59)),
    (0xF0000000, (0, 1, 2, 4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 17, 18, 19, 20, 21, 22, 23)),
)


def cases():
    return [
        Case(
            f"{prefix + offset:08x}",
            {"id": f"{prefix + offset:08x}", "text": f"private-{prefix}-{i}", "weight": 1 + i / 20},
        )
        for prefix, offsets in CASE_OFFSETS
        for i, offset in enumerate(offsets)
    ]


def agent(case, prompts):
    version = int(prompts["system"].splitlines()[0].split(": ")[1])
    return min(version, 2) * case["weight"]


def proposals(versions=(1, 2, 3), *, seen=None, edit=None):
    versions = iter(versions)
    patterns = llm_stub()

    def llm(prompt):
        if seen is not None:
            seen.append(prompt)
        if "<examples>" not in prompt:
            return patterns(prompt)
        text = edit or f"version: {next(versions)}\nFollow the rules.\n"
        return json.dumps(
            {
                "prompts": {"system": text},
                "rationale": "Improve the version.",
                "cited_case_ids": [block(prompt, "examples")[0][0]],
            }
        )

    return llm


def run(tmp_path, *, llm=None, function=agent, data=None, **settings):
    options = {
        "directory": tmp_path,
        "score_fn": lambda case, out: out,
        "samples": 1,
        "min_pairs": 5,
        "holdout_share": 0.5,
        **settings,
    }
    return evolve(
        function_agent(function),
        cases() if data is None else data,
        PROMPTS,
        llm or proposals(),
        **options,
    )


def test_split_is_deterministic_disjoint_complete_ordered_and_outcome_blind():
    data = cases()
    learning, held = split_cases(data, 0.3)
    assert split_cases(data, 0.3) == (learning, held)
    assert len(held) == int(0.3 * len(data) + 0.5)
    assert set(c.id for c in learning).isdisjoint(c.id for c in held)
    assert len(learning) + len(held) == len(data)
    changed = [replace(c, input={"score": -999, "text": "changed"}) for c in data]
    assert [[c.id for c in part] for part in split_cases(changed, 0.3)] == [
        [c.id for c in learning],
        [c.id for c in held],
    ]


@pytest.mark.parametrize("n,share,held", [(20, 0.5, 10), (40, 0.5, 20), (25, 0.3, 8), (5, 0.5, 3)])
def test_split_holds_out_an_exact_count_of_lowest_hashes(n, share, held):
    from hashlib import sha256

    data = [Case(f"customer-{i}", {}) for i in range(n)]
    learning, holdout = split_cases(data, share)
    assert len(holdout) == held and len(learning) == n - held
    lowest = sorted(data, key=lambda case: sha256(case.id.encode()).digest())[:held]
    assert {case.id for case in holdout} == {case.id for case in lowest}


def test_split_accepts_arbitrary_short_and_unicode_ids():
    data = [Case(key, {"value": i}) for i, key in enumerate(("", "x", "customer-12", "你好", "é"))]
    first = split_cases(data, 0.5)
    assert split_cases(data, 0.5) == first
    assert sum(map(len, first)) == len(data)
    changed = [replace(case, input={"different": True}) for case in data]
    assert [[case.id for case in part] for part in split_cases(changed, 0.5)] == [
        [case.id for case in part] for part in first
    ]


@pytest.mark.parametrize("share", [0.3, 0.5, 0.7])
def test_split_hash_is_uniform_over_synthetic_ids(share):
    data = [Case(f"customer-{i}", {}) for i in range(1000)]
    learning, held = split_cases(data, share)
    assert abs(len(held) / len(data) - share) <= 0.05
    assert set(case.id for case in learning).isdisjoint(case.id for case in held)


@pytest.mark.parametrize("rounds,expected", [(1, 0.95), (5, 0.99), (100, 0.999)])
def test_round_confidence_exact_values(rounds, expected):
    assert round_confidence(0.95, rounds) == expected


def test_evolve_accepts_two_good_rounds_then_stops_at_useless_third(tmp_path):
    result = run(tmp_path)
    assert result.accepted == 2 and len(result.rounds) == 3
    assert result.final_prompts["system"].startswith("version: 2\n")
    assert [r.index for r in result.rounds] == [1, 2, 3]
    assert [r.verdict.decision for r in result.rounds] == ["better", "better", "inconclusive"]
    assert [r.accepted for r in result.rounds] == [True, True, False]
    assert result.rounds[1].prompts_before == result.rounds[0].proposal.prompts
    assert result.calls == sum(r.calls for r in result.rounds) == 186
    assert not result.budget_exhausted
    assert PROMPTS["system"].startswith("version: 0")
    with pytest.raises(FrozenInstanceError):
        result.accepted = 4
    with pytest.raises(FrozenInstanceError):
        result.rounds[0].accepted = False


def test_learning_only_overfit_is_rejected_by_held_out_gate(tmp_path):
    learning, _ = split_cases(cases(), 0.5)
    edit = PROMPTS["system"] + "reward_ids: " + " ".join(c.id for c in learning)

    def memorizing(case, prompts):
        return int(case["id"] in prompts["system"])

    result = run(tmp_path, function=memorizing, llm=proposals(edit=edit))
    assert all(memorizing(c.input, {"system": edit}) == 1 for c in learning)
    assert result.accepted == 0 and result.final_prompts == PROMPTS
    assert len(result.rounds) == 1
    assert result.rounds[0].verdict.decision == "inconclusive"
    assert result.rounds[0].verdict.mean == 0


def test_proposal_and_pattern_requests_never_contain_holdout_ids_or_inputs(tmp_path):
    seen = []
    result = run(tmp_path, llm=proposals(seen=seen))
    requests = "\n".join(seen)
    # Match run(): int(0.5 * 40 + 0.5) = 20 held-out IDs, the first CASE_OFFSETS group.
    _, held = split_cases(cases(), 0.5)
    assert len([p for p in seen if "<examples>" in p]) == 3
    assert all(c.id not in requests and c.input["text"] not in requests for c in held)
    assert result.holdout_ids == [c.id for c in held]


def test_every_round_gate_uses_adjusted_confidence(tmp_path):
    result = run(tmp_path, rounds=5)
    _, held = split_cases(cases(), 0.5)
    expected = decide([c.input["weight"] for c in held], min_pairs=5, confidence=0.99)
    assert result.confidence_per_round == 0.99
    assert result.rounds[0].verdict == result.rounds[1].verdict == expected
    assert expected.ci_low != decide([c.input["weight"] for c in held], min_pairs=5).ci_low


@pytest.mark.parametrize(
    "settings,message",
    [
        ({"rounds": 0}, "rounds must be at least 1"),
        ({"rounds": 1.5}, "rounds must be at least 1"),
        ({"holdout_share": 0}, "holdout_share must be between 0 and 1 (exclusive)"),
        ({"holdout_share": 1}, "holdout_share must be between 0 and 1 (exclusive)"),
        ({"holdout_share": float("nan")}, "holdout_share must be between 0 and 1 (exclusive)"),
        (
            {"min_pairs": 21},
            "need at least 21 held-out cases, got 20; add cases or raise holdout_share",
        ),
        ({"confidence": 1}, "confidence must be between 0.5 and 0.999"),
    ],
)
def test_evolve_validation_has_exact_messages(tmp_path, settings, message):
    with pytest.raises(ValueError) as error:
        run(tmp_path, **settings)
    assert str(error.value) == message


def test_evolve_requires_learning_cases(tmp_path):
    # int(0.9 * 5 + 0.5) = 5: satisfy min_pairs while leaving no learning cases.
    with pytest.raises(ValueError, match="need at least 1 learning case, got 0"):
        run(tmp_path, data=cases()[:5], holdout_share=0.9)


@pytest.mark.parametrize("budget,completed", [(0, 0), (21, 0), (61, 0), (62, 1), (100, 1)])
def test_evolve_budget_keeps_only_completed_rounds_never_partial_acceptance(
    tmp_path, budget, completed
):
    result = run(tmp_path, max_calls=budget)
    assert result.budget_exhausted
    assert len(result.rounds) == result.accepted == completed
    assert result.calls <= budget
    assert result.final_prompts["system"].startswith(f"version: {completed}\n")


def test_evolve_round_limit_completes_at_exact_budget(tmp_path):
    result = run(tmp_path, rounds=1, max_calls=62)
    assert result.accepted == len(result.rounds) == 1
    assert not result.budget_exhausted


def test_evolve_rejected_edit_stops_without_gate(tmp_path):
    result = run(tmp_path, llm=proposals(edit=PROMPTS["system"]))
    assert result.accepted == 0 and len(result.rounds) == 1
    assert result.rounds[0].verdict is None
    assert "actually be changed" in result.rounds[0].rejection


def test_evolve_report_includes_lineage_stats_and_each_diff(tmp_path):
    result = run(tmp_path)
    report = evolve_report(result, originals=PROMPTS, version="test")
    assert report.startswith("Evolved: 2 of 3 rounds passed on held-out cases (99.0% per round).\n")
    assert "Learning cases: 20; held-out cases: 20" in report
    assert all(f"Round {i}" in report for i in (1, 2, 3))
    assert report.count("Improve the version.") == 3
    assert report.count("Cited cases:") == 3
    assert "99% CI" in report and "95% CI" not in report
    assert "-version: 0" in report and "+version: 2" in report and "+version: 3" in report
    assert "Calls: 186" in report and "version: test" in report
    assert "never shown" in report and "99.0%" in report


def test_evolve_report_no_improvement_and_budget_headlines(tmp_path):
    result = run(tmp_path, llm=proposals(edit="version: -1\nFollow the rules.\n"))
    report = evolve_report(result, originals=PROMPTS, version="test")
    assert report.startswith("No improvement passed on held-out cases (1 round tried).\n")
    limited = run(tmp_path, max_calls=1)
    assert "budget" in evolve_report(limited, originals=PROMPTS, version="test").lower()


def test_evolve_report_preserves_agent_errors_and_model_provenance(tmp_path):
    def broken(case, prompts):
        raise ValueError("specific agent failure")

    llm = proposals()
    llm.model = "stub-model"
    result = run(tmp_path, function=broken, llm=llm)
    report = evolve_report(result, originals=PROMPTS, version="test")
    assert result.accepted == 0
    assert "specific agent failure" in report and "stub-model" in report


def test_evolve_judge_mode_stops_without_proposal_when_learning_has_no_failures(tmp_path):
    result = run(tmp_path, score_fn=None, samples=2, llm=llm_stub(pass_grades=True))
    assert result.accepted == 0 and len(result.rounds) == 1
    assert result.rounds[0].proposal is result.rounds[0].verdict is None
    assert result.calls == 60


def test_evolve_trial_is_durable_if_later_proposal_call_errors(tmp_path):
    from promptcheckup.llm import LLMError

    first = proposals(versions=(1,))
    seen = []

    def llm(prompt):
        seen.append(prompt)
        return first(prompt) if len(seen) <= 2 else "{}"

    with pytest.raises(LLMError):
        run(tmp_path, llm=llm, trial_command="evolve")
    rows = (tmp_path / ".promptcheckup/trials.jsonl").read_text().splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["decision"] == "better"


def test_evolve_reports_held_out_baseline_errors(tmp_path):
    def broken_baseline(case, prompts):
        if case["id"].startswith("0") and prompts == PROMPTS:
            raise ValueError("held-out baseline unavailable")
        return agent(case, prompts)

    result = run(tmp_path, function=broken_baseline)
    assert result.accepted == 0
    report = evolve_report(result, originals=PROMPTS, version="test")
    assert "held-out baseline unavailable" in report


def test_evolve_report_shows_confidence_to_one_decimal(tmp_path):
    result = replace(run(tmp_path), confidence_per_round=round_confidence(0.95, 3))
    report = evolve_report(result, originals=PROMPTS, version="test")

    assert "98.3% CI" in report and "98.3333" not in report
