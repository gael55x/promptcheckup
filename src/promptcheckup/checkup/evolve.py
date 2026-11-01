"""Bounded prompt evolution: learn on fixed cases, gate on a disjoint holdout."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import reduce
from itertools import repeat

from promptcheckup.checkup.checkup import propose_only
from promptcheckup.checkup.split import split_cases, valid_holdout_share
from promptcheckup.data import Case
from promptcheckup.llm.propose import Proposal
from promptcheckup.runner import Agent, Run
from promptcheckup.stats import Verdict

OPTION_RULES = (
    (
        lambda rounds, share, confidence: type(rounds) is int and rounds >= 1,
        "rounds must be at least 1",
    ),
    (
        lambda rounds, share, confidence: valid_holdout_share(share),
        "holdout_share must be between 0 and 1 (exclusive)",
    ),
    (
        lambda rounds, share, confidence: (
            type(confidence) in (int, float) and 0.5 <= confidence <= 0.999
        ),
        "confidence must be between 0.5 and 0.999",
    ),
)
SPLIT_RULES = (
    (
        lambda learning, held, minimum: len(held) >= minimum,
        lambda learning, held, minimum: (
            f"need at least {minimum} held-out cases, got {len(held)}; "
            "add cases or raise holdout_share"
        ),
    ),
    (
        lambda learning, held, minimum: bool(learning),
        lambda learning, held, minimum: "need at least 1 learning case, got 0",
    ),
)


@dataclass(frozen=True)
class Round:
    index: int
    prompts_before: dict[str, str]
    proposal: Proposal | None
    rejection: str | None
    verdict: Verdict | None
    accepted: bool
    calls: int


@dataclass(frozen=True)
class EvolveResult:
    rounds: list[Round]
    final_prompts: dict[str, str]
    accepted: int
    calls: int
    budget_exhausted: bool
    confidence_per_round: float
    learning_ids: list[str]
    holdout_ids: list[str]
    errors: list[Run] = field(default_factory=list)
    model: str = ""


@dataclass(frozen=True)
class _Plan:
    agent: Agent
    learning: list[Case]
    holdout: list[Case]
    llm: Callable[[str], str]
    settings: dict
    max_calls: int


def round_confidence(base, rounds) -> float:
    """Bonferroni confidence; the 0.999 cap makes the guarantee approximate above
    50 rounds at base 0.95. Reusing the holdout still leaks acceptance information.
    """
    return min(0.999, 1 - (1 - base) / rounds)


def evolution_cases(cases, *, rounds=5, holdout_share=0.3, confidence=0.95, min_pairs=20):
    """Validate before any calls; shared with the CLI's cold-cache call planning."""
    for predicate, message in OPTION_RULES:
        if not predicate(rounds, holdout_share, confidence):
            raise ValueError(message)
    learning, holdout = split_cases(cases, holdout_share)
    for predicate, message in SPLIT_RULES:
        if not predicate(learning, holdout, min_pairs):
            raise ValueError(message(learning, holdout, min_pairs))
    return learning, holdout


def evolve(
    agent, cases, prompts, llm, *, rounds=5, holdout_share=0.3, confidence=0.95, **settings
) -> EvolveResult:
    """Run the existing proposal pipeline, stopping at rejection or the shared call cap.

    Settings are those of propose_only. Only completed rounds enter the lineage;
    calls spent on an incomplete round still count against the run's budget.
    """
    learning, holdout = evolution_cases(
        cases,
        rounds=rounds,
        holdout_share=holdout_share,
        confidence=confidence,
        min_pairs=settings.get("min_pairs", 20),
    )
    plan = _Plan(agent, learning, holdout, llm, settings, settings.get("max_calls", 400))
    initial = EvolveResult(
        [],
        dict(prompts),
        0,
        0,
        False,
        round_confidence(confidence, rounds),
        [c.id for c in learning],
        [c.id for c in holdout],
        model=getattr(llm, "model", ""),
    )
    return reduce(lambda state, step: step(plan, state), repeat(_next_round, rounds), initial)


def _next_round(plan, state):
    if state.budget_exhausted or (state.rounds and not state.rounds[-1].accepted):
        return state
    if state.calls >= plan.max_calls:
        return replace(state, budget_exhausted=True)
    result = propose_only(
        plan.agent,
        plan.learning,
        state.final_prompts,
        plan.llm,
        **{
            **plan.settings,
            "gate_cases": plan.holdout,
            "confidence": state.confidence_per_round,
            "max_calls": plan.max_calls - state.calls,
        },
    )
    spent = replace(state, calls=state.calls + result.calls, errors=[*state.errors, *result.errors])
    if result.budget_exhausted:
        return replace(spent, budget_exhausted=True)
    return _completed(spent, result)


def _completed(state, result):
    accepted = result.proposal is not None
    completed = Round(
        len(state.rounds) + 1,
        dict(state.final_prompts),
        result.gated_proposal,
        result.proposal_rejection,
        result.proposal_verdict,
        accepted,
        result.calls,
    )
    prompts = dict(result.proposal.prompts) if accepted else state.final_prompts
    return replace(
        state,
        rounds=[*state.rounds, completed],
        final_prompts=prompts,
        accepted=state.accepted + int(accepted),
    )
