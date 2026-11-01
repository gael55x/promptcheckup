"""Deterministic, outcome-blind learning and holdout sets."""

from hashlib import sha256

from promptcheckup.data import Case


def valid_holdout_share(share) -> bool:
    return type(share) in (int, float) and 0 < share < 1


def split_cases(cases, holdout_share) -> tuple[list[Case], list[Case]]:
    """Hold out the round(share × n) cases whose IDs hash lowest, preserving order.

    An exact count, not a per-case hash threshold, so 40 cases at 0.5 always hold out 20.
    """
    if not valid_holdout_share(holdout_share):
        raise ValueError("holdout_share must be between 0 and 1 (exclusive)")
    ranked = sorted(cases, key=lambda case: sha256(case.id.encode()).digest())
    held_ids = {case.id for case in ranked[: int(holdout_share * len(cases) + 0.5)]}
    learning = [case for case in cases if case.id not in held_ids]
    return learning, [case for case in cases if case.id in held_ids]
