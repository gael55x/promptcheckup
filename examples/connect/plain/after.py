from typing import Any

from plain_agent import answer


def run(case: dict, prompts: dict[str, str]) -> Any:
    prompt = prompts["prompts/system.md"]
    return answer(case["question"], prompt)
