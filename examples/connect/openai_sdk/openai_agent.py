from openai import OpenAI


def run(case: dict, prompts: dict[str, str]) -> str:
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {"role": "system", "content": prompts["prompts/system.md"]},
            {"role": "user", "content": case["question"]},
        ],
    )
    return response.choices[0].message.content
