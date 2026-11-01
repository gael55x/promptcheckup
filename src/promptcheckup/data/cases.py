"""Read and append labelled cases as JSONL."""

import hashlib
import json
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Source = Literal["recorded", "synthetic", "manual"]

_write_lock = threading.Lock()


@dataclass(frozen=True)
class Case:
    id: str
    input: dict[str, Any]
    source: Source = "manual"
    output: Any = None


class CaseError(ValueError):
    """Invalid case data, with file and line when read from JSONL."""


def case_id(input: dict[str, Any]) -> str:
    """Return a stable, short hash of the input's canonical JSON."""
    canonical = json.dumps(input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def load_cases(patterns: Sequence[str], root: Path = Path(".")) -> list[Case]:
    """Load each matching file once, ordered by path and then line number."""
    paths = set()
    for pattern in patterns:
        matches = {path for path in root.glob(pattern) if path.is_file()}
        if not matches:
            raise CaseError(f"no files match pattern {pattern!r} under {root}")
        paths.update(matches)

    cases = []
    locations = {}
    for path in sorted(paths):
        with path.open(encoding="utf-8") as file:
            for number, line in enumerate(file, start=1):
                if not line.strip():
                    continue
                location = f"{path}:{number}"
                try:
                    row = json.loads(line)
                except ValueError as e:
                    raise CaseError(f"{location}: invalid JSON: {e}") from e
                if not isinstance(row, dict):
                    raise CaseError(f"{location}: case must be a JSON object")
                unknown = row.keys() - {"id", "input", "source", "output"}
                if unknown:
                    raise CaseError(f"{location}: unknown keys: {', '.join(sorted(unknown))}")
                if not isinstance(row.get("input"), dict):
                    raise CaseError(f"{location}: input is required and must be a JSON object")
                if "id" in row and not isinstance(row["id"], str):
                    raise CaseError(f"{location}: id must be a string")
                source = row.get("source", "manual")
                if source not in ("recorded", "synthetic", "manual"):
                    raise CaseError(f"{location}: source must be recorded, synthetic or manual")

                identifier = row["id"] if "id" in row else case_id(row["input"])
                if identifier in locations:
                    raise CaseError(
                        f"{location}: duplicate id {identifier!r}; first at {locations[identifier]}"
                    )
                locations[identifier] = location
                cases.append(Case(identifier, row["input"], source, row.get("output")))
    return cases


def append_cases(path: Path, cases: Iterable[Case]) -> None:
    """Append complete JSON lines, serializing writes across threads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        row = {"id": case.id, "input": case.input, "source": case.source}
        if case.output is not None:
            row["output"] = case.output
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _write_lock, path.open("a", encoding="utf-8") as file:
            file.write(line)
