"""Shared prompt plumbing: embed untrusted data safely and parse strict JSON replies."""

import json
import re
from collections.abc import Callable, Mapping
from typing import Any

from promptcheckup.errors import fallback

# A closing ```json (or bare ```) fence that ends the reply.
FINAL_FENCE = re.compile(r"```[ \t]*(?:json)?[ \t]*\n(.*)\n[ \t]*```\Z", re.DOTALL | re.IGNORECASE)

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
    """Parse the JSON object that ends a reply, optionally inside a final code fence.

    Models may reason before answering, so prose may come first; nothing may follow.
    Anything else raises ValueError: a judgment is never guessed from prose.
    """
    text = reply.strip()
    fenced = FINAL_FENCE.search(text)
    return _final_object(fenced.group(1).strip() if fenced else text)


def _final_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    candidates = (_decoded(decoder, text, i) for i, char in enumerate(text) if char == "{")
    found = next((value for value, end in filter(None, candidates) if end == len(text)), None)
    if found is None:
        raise ValueError("reply does not end with a JSON object")
    return found


def _decoded(decoder: json.JSONDecoder, text: str, start: int) -> tuple[Any, int] | None:
    return fallback(lambda: decoder.raw_decode(text, start), lambda error: None, ValueError)
