from typing import Any


def answer(question: str, prompt: str) -> dict:
    return {"answer": f"{prompt.strip()} {question}!"}


def run(case: dict, prompts: dict[str, str]) -> Any:
    prompt = prompts["prompts/system.md"]
    return answer(case["question"], prompt)


def score(case: dict, output: Any) -> float:
    return float(isinstance(output, dict) and output.get("answer") == f"Hello, {case['question']}!")
