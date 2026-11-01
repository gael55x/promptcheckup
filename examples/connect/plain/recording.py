from pathlib import Path

from plain_agent import run

from promptcheckup import record


def redact(value: dict) -> dict:
    return {key: item for key, item in value.items() if key != "email"}


@record("cases/recorded.jsonl", redact=redact)
def handle(case: dict) -> dict:
    prompts = {"prompts/system.md": Path("prompts/system.md").read_text(encoding="utf-8")}
    return run(case, prompts)


if __name__ == "__main__":
    print(handle({"question": "Ada", "email": "ada@example.invalid"}))
