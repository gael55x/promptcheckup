"""Shared prompt plumbing: embed untrusted data safely and parse strict JSON replies."""

import json
from collections.abc import Callable, Mapping
from typing import Any

REPLY_RULES = (
    (lambda result, schema: result.keys() == schema.keys(), "reply has unexpected keys"),
    (
        lambda result, schema: all(check(result[key]) for key, check in schema.items()),
        "reply has invalid field values",
    ),
)


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validated_reply(reply: str, schema: Mapping[str, Callable[[Any], bool]]) -> dict:
    """Parse a JSON reply and require exactly the fields and values in the schema."""
    result = json_reply(reply)
    for predicate, message in REPLY_RULES:
        if not predicate(result, schema):
            raise ValueError(message)
    return result


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
