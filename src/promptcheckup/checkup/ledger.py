"""Append-only audit of gated variants; never use trial counts to change a verdict."""

import json
from datetime import UTC, datetime
from pathlib import Path

from promptcheckup.errors import fallback
from promptcheckup.runner import prompt_hash

TRIAL_FIELDS = ("case_set", "prompt_a", "prompt_b", "decision", "command", "at")


def record_trial(directory, *, case_ids, prompt_a, prompt_b, decision, confidence, command):
    """Append one comparison, accepting the already computed A and B prompt hashes."""
    path = _path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(
        case_set=_case_set(case_ids),
        prompt_a=prompt_a,
        prompt_b=prompt_b,
        decision=decision,
        confidence=confidence,
        command=command,
        at=datetime.now(UTC).isoformat(),
    )
    _append_line(path, json.dumps(entry, ensure_ascii=False))


def trials(directory, case_ids) -> int:
    """Count distinct proposed prompt hashes on exactly this (order-independent) case set."""
    path = _path(directory)
    if not path.exists():
        return 0
    case_set = _case_set(case_ids)
    with path.open("rb") as stream:
        entries = [_read_entry(line) for line in stream]
    return len(
        {
            entry["prompt_b"]
            for entry in entries
            if entry is not None and entry["case_set"] == case_set
        }
    )


def false_better_expected(n, confidence) -> float:
    """Expected one-sided false 'better' verdicts under the nominal two-sided interval."""
    return n * (1 - confidence) / 2


def _path(directory):
    return Path(directory) / ".promptcheckup" / "trials.jsonl"


def _case_set(case_ids):
    return prompt_hash(sorted(case_ids))


def _read_entry(line):
    return fallback(
        lambda: _decoded_entry(line), lambda error: None, ValueError, KeyError, TypeError
    )


def _decoded_entry(line):
    entry = json.loads(line)
    if not all(isinstance(entry[key], str) for key in TRIAL_FIELDS):
        raise ValueError("damaged trial entry")
    if type(entry["confidence"]) not in (int, float) or not 0.5 <= entry["confidence"] <= 0.999:
        raise ValueError("damaged trial confidence")
    return entry


def _append_line(path, line):
    # A crashed write may leave a damaged tail without its newline. Preserve it,
    # but put the next trial on its own line so the reader can recover that trial.
    with path.open("ab+") as stream:
        if stream.tell():
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                stream.write(b"\n")
        stream.write((line + "\n").encode("utf-8"))
