from typing import Any


def score(case: dict, output: Any) -> float:
    return float(isinstance(output, dict) and output.get("answer") == f"Hello, {case['question']}!")
