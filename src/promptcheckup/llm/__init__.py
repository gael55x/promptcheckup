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

__all__ = [
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
