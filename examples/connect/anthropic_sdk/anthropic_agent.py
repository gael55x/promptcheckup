from anthropic import Anthropic


def run(case: dict, prompts: dict[str, str]) -> str:
    client = Anthropic()
    response = client.messages.create(
        model="claude-haiku-5-5",
        max_tokens=1024,
        system=prompts["prompts/system.md"],
        messages=[{"role": "user", "content": case["question"]}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
