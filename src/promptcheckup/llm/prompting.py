"""Shared prompt plumbing: embed untrusted data safely and parse strict JSON replies."""

import json
from typing import Any


def data_block(value: Any) -> str:
    """JSON for embedding untrusted data in a prompt.

    `<` and `>` are escaped (still valid JSON) so data cannot close its own block and
    inject instructions, for example new criteria written by the agent under test.
    """
    text = json.dumps(value, ensure_ascii=False)
    return text.replace("<", "\\u003c").replace(">", "\\u003e")


def json_reply(reply: str) -> dict:
    """Parse a reply that is a JSON object, optionally inside a code fence.

    Anything else raises ValueError: a judgment is never guessed from prose.
    """
    lines = reply.strip().splitlines()
    if (
        len(lines) >= 3
        and lines[0].strip().lower() in ("```", "```json")
        and lines[-1].strip() == "```"
    ):
        lines = lines[1:-1]
    result = json.loads("\n".join(lines))
    if not isinstance(result, dict):
        raise ValueError("reply is not a JSON object")
    return result
