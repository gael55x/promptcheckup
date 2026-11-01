"""Call an OpenAI-compatible endpoint, grade outputs and judge paired responses."""

from promptcheckup.llm.client import LLM, LLMError, Usage
from promptcheckup.llm.grade import Grade, grade
from promptcheckup.llm.judge import (
    Comparison,
    Preference,
    SelfCheck,
    compare,
    judge_pairs,
    paired_scores,
    pairs_from_replay,
    self_check,
)
from promptcheckup.llm.patterns import Pattern, failure_patterns
from promptcheckup.llm.propose import Proposal, ProposalRejected, propose
from promptcheckup.llm.starter import starter_cases

__all__ = [
    "Pattern",
    "Proposal",
    "ProposalRejected",
    "failure_patterns",
    "propose",
    "starter_cases",
    "LLM",
    "LLMError",
    "Comparison",
    "Grade",
    "Preference",
    "SelfCheck",
    "Usage",
    "compare",
    "grade",
    "judge_pairs",
    "paired_scores",
    "pairs_from_replay",
    "self_check",
]
