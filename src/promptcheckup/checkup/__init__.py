"""Shared evaluation and human-readable reports."""

from promptcheckup.checkup.checkup import CheckupResult, checkup, propose_only
from promptcheckup.checkup.evaluate import (
    Evaluation,
    Scorer,
    Scores,
    evaluate,
    evaluate_replay,
    judge_mode,
    planned_calls,
    redacted_inputs,
    score_mode,
)
from promptcheckup.checkup.evolve import (
    EvolveResult,
    Round,
    evolution_cases,
    evolve,
    round_confidence,
    split_cases,
)
from promptcheckup.checkup.ledger import false_better_expected, record_trial, trials
from promptcheckup.checkup.report import checkup_report, evolve_report, gate_report, proposal_report
from promptcheckup.checkup.snapshot import Snapshot, load_snapshot, save_snapshot

__all__ = [
    "EvolveResult",
    "Round",
    "evolution_cases",
    "evolve",
    "split_cases",
    "round_confidence",
    "evolve_report",
    "record_trial",
    "trials",
    "false_better_expected",
    "CheckupResult",
    "Snapshot",
    "checkup",
    "checkup_report",
    "evaluate_replay",
    "load_snapshot",
    "proposal_report",
    "propose_only",
    "save_snapshot",
    "Evaluation",
    "Scorer",
    "Scores",
    "evaluate",
    "gate_report",
    "judge_mode",
    "planned_calls",
    "redacted_inputs",
    "score_mode",
]
