"""Persist the previous check-up's evidence under its prompt hash."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from promptcheckup.errors import fallback
from promptcheckup.llm.prompting import validated_reply
from promptcheckup.runner import Run

SNAPSHOT_SCHEMA = {
    "created_at": lambda value: isinstance(value, str),
    "agent_version": lambda value: isinstance(value, str),
    "runs": lambda value: isinstance(value, list),
}
RUN_SCHEMA = {
    "case_id": lambda value: isinstance(value, str),
    "prompt_set": lambda value: value == "A",
    "sample": lambda value: type(value) is int and value >= 0,
    "output": lambda value: True,
    "error": lambda value: value is None or isinstance(value, str),
    "cached": lambda value: type(value) is bool,
}


@dataclass(frozen=True)
class Snapshot:
    created_at: str
    agent_version: str
    runs: list[Run]


def save_snapshot(directory, prompts_hash, runs, agent_version) -> None:
    """Write a complete snapshot; I/O failures propagate to the command boundary."""
    path = _path(directory, prompts_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    snapshot = Snapshot(datetime.now(UTC).isoformat(), agent_version, runs)
    path.write_text(json.dumps(asdict(snapshot), ensure_ascii=False) + "\n", encoding="utf-8")


def load_snapshot(directory, prompts_hash) -> Snapshot | None:
    """Missing or damaged evidence is a cache miss, never a fabricated baseline."""
    return fallback(
        lambda: _read_snapshot(_path(directory, prompts_hash)),
        lambda error: None,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    )


def load_latest_snapshot(directory, *, excluding) -> tuple[str, Snapshot] | None:
    """Return the newest valid evidence from another prompt, or a cache miss."""
    candidates = []
    for path in sorted(_path(directory, excluding).parent.glob("*.json")):
        if path.stem == excluding:
            continue
        snapshot = load_snapshot(directory, path.stem)
        if snapshot is not None:
            candidates.append((path.stem, snapshot))
    return max(candidates, key=lambda item: _created_at(item[1]), default=None)


def _created_at(snapshot):
    timestamp = datetime.fromisoformat(snapshot.created_at)
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def _path(directory, prompts_hash):
    return Path(directory) / ".promptcheckup" / "snapshots" / f"{prompts_hash}.json"


def _read_snapshot(path) -> Snapshot:
    data = validated_reply(path.read_text(encoding="utf-8"), SNAPSHOT_SCHEMA)
    datetime.fromisoformat(data["created_at"])
    runs = [Run(**validated_reply(json.dumps(run), RUN_SCHEMA)) for run in data["runs"]]
    if len({(run.case_id, run.sample) for run in runs}) != len(runs):
        raise ValueError("duplicate snapshot runs")
    return Snapshot(data["created_at"], data["agent_version"], runs)
