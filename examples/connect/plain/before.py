from pathlib import Path

from plain_agent import answer


def run(case: dict) -> dict:
    prompt = Path("prompts/system.md").read_text(encoding="utf-8")
    return answer(case["question"], prompt)
