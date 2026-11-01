"""Call an OpenAI-compatible endpoint and grade outputs against user criteria."""

from promptcheckup.llm.client import LLM, LLMError, Usage
from promptcheckup.llm.grade import Grade, grade

__all__ = ["LLM", "LLMError", "Grade", "Usage", "grade"]
