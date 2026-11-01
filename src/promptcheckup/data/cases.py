"""Read and append labelled cases as JSONL."""

import hashlib
import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from promptcheckup.errors import errors_as

Source = Literal["recorded", "synthetic", "manual"]

CASE_FIELDS = frozenset(("id", "input", "source", "output"))
ROW_RULES = (
    (lambda row: isinstance(row, dict), lambda row: "case must be a JSON object"),
    (
        lambda row: not row.keys() - CASE_FIELDS,
        lambda row: f"unknown keys: {', '.join(sorted(row.keys() - CASE_FIELDS))}",
    ),
    (
        lambda row: isinstance(row.get("input"), dict),
        lambda row: "input is required and must be a JSON object",
    ),
    (
        lambda row: "id" not in row or isinstance(row["id"], str),
        lambda row: "id must be a string",
    ),
    (
        lambda row: row.get("source", "manual") in ("recorded", "synthetic", "manual"),
        lambda row: "source must be recorded, synthetic or manual",
    ),
)


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
    paths = sorted({path for pattern in patterns for path in _matching_paths(pattern, root)})
    rows = (row for path in paths for row in _case_lines(path))
    return _unique_cases(rows)


def _matching_paths(pattern: str, root: Path) -> set[Path]:
    matches = {path for path in root.glob(pattern) if path.is_file()}
    if not matches:
        raise CaseError(f"no files match pattern {pattern!r} under {root}")
    return matches


def _case_lines(path: Path) -> Iterator[tuple[str, str]]:
    with path.open(encoding="utf-8") as file:
        for number, line in enumerate(file, start=1):
            if line.strip():
                yield line, f"{path}:{number}"


def _validated_case(line: str, location: str) -> Case:
    with errors_as(CaseError, {ValueError: lambda error: f"{location}: invalid JSON: {error}"}):
        row = json.loads(line)
    for predicate, message in ROW_RULES:
        if not predicate(row):
            raise CaseError(f"{location}: {message(row)}")
    identifier = row["id"] if "id" in row else case_id(row["input"])
    return Case(identifier, row["input"], row.get("source", "manual"), row.get("output"))


def _unique_cases(rows: Iterable[tuple[str, str]]) -> list[Case]:
    locations = {}
    cases = []
    for line, location in rows:
        case = _validated_case(line, location)
        if case.id in locations:
            raise CaseError(f"{location}: duplicate id {case.id!r}; first at {locations[case.id]}")
        locations[case.id] = location
        cases.append(case)
    return cases


def append_cases(path: Path, cases: Iterable[Case]) -> None:
    """Append each complete JSON line with one unbuffered append write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        line = _case_line(case).encode("utf-8")
        # O_APPEND positions and writes a whole line in one system call, with no
        # shared Python lock or text-buffer flushes that could interleave writers.
        with path.open("ab", buffering=0) as file:
            if file.write(line) != len(line):
                raise OSError("could not append complete case")


def _case_line(case: Case) -> str:
    row = {"id": case.id, "input": case.input, "source": case.source}
    if case.output is not None:
        row["output"] = case.output
    return json.dumps(row, ensure_ascii=False) + "\n"
